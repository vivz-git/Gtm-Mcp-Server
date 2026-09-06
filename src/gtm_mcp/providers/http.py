"""Outbound HTTP for enrichment adapters.

Every enrichment provider is a paid, rate-limited, occasionally flaky external
dependency, so the transport concerns that protect us from all three live here
once rather than in each adapter: a hard timeout, a bounded retry budget, and a
retry predicate narrow enough that it can never multiply spend.

**What is retried:** transport failures (connection refused, read timeout) and
5xx responses. Nothing else.

**What is deliberately not retried:**

* ``429`` and any other quota or rate-limit response. Retrying a rate limit is
  how a client turns a temporary block into a longer one, and on a metered API a
  retried call can be a second charge.
* Every other ``4xx``. A rejected credential or a malformed query fails the same
  way on the second attempt; the only thing a retry adds is cost.

The adapter, not this module, decides what a given ``4xx`` *means* — Hunter's
``403`` is a rate limit while its ``429`` is a monthly quota, and that mapping is
vendor knowledge that belongs in the vendor's adapter. This module therefore
hands back any non-5xx response and interprets only the failures that are
transport-level.

Nothing here logs a header, a credential or a query string.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

import anyio
import httpx2

from gtm_mcp.errors import ProviderError
from gtm_mcp.logging_setup import get_logger

_log = get_logger(__name__)

#: Transport-level failures worth another attempt. A DNS or TLS failure is not
#: in this set: neither is fixed by trying again a second later.
RETRYABLE_TRANSPORT_ERRORS: Final[tuple[type[httpx2.RequestError], ...]] = (
    httpx2.ConnectError,
    httpx2.ConnectTimeout,
    httpx2.ReadTimeout,
    httpx2.WriteTimeout,
    httpx2.PoolTimeout,
    httpx2.RemoteProtocolError,
)


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """A decoded provider response, ready for vendor-specific interpretation."""

    status_code: int
    """HTTP status returned by the provider. Never 5xx: those are retried, then raised."""

    payload: Mapping[str, Any]
    """The decoded JSON object. An empty mapping when the body was empty."""


async def _default_sleep(seconds: float) -> None:
    """Suspend the current task.

    Indirected so tests can assert on backoff without waiting for it.

    Args:
        seconds: How long to sleep.
    """
    await anyio.sleep(seconds)


class EnrichmentHttpClient:
    """A thin, retry-bounded JSON client shared by enrichment adapters.

    Wraps a single ``httpx2.AsyncClient`` owned by the server lifespan, so
    connections are pooled across tool calls rather than re-established per
    request.
    """

    def __init__(
        self,
        client: httpx2.AsyncClient,
        *,
        provider: str,
        max_retries: int,
        backoff_seconds: float,
        sleep: Callable[[float], Awaitable[None]] = _default_sleep,
    ) -> None:
        """Initialise the client.

        Args:
            client: The shared async HTTP client.
            provider: Provider identifier, used only for log correlation.
            max_retries: Additional attempts after the first. ``0`` disables
                retrying entirely.
            backoff_seconds: Base delay; attempt *n* waits
                ``backoff_seconds * 2 ** (n - 1)``. Deterministic rather than
                jittered, so a test can assert the schedule exactly.
            sleep: Injection point for the backoff delay.
        """
        self._client = client
        self._provider = provider
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep

    async def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> ProviderResponse:
        """Issue a GET and decode the JSON body.

        Args:
            url: Absolute request URL.
            params: Query parameters. Must not carry a credential; pass those in
                ``headers`` so they cannot leak through a log or a proxy access
                log.
            headers: Request headers, including authentication.

        Returns:
            The status code and decoded payload, for the adapter to interpret.

        Raises:
            ProviderError: The provider was unreachable, kept returning 5xx
                until the retry budget was spent, or returned a body that was
                not a JSON object.
        """
        attempts = self._max_retries + 1
        last_reason = "unknown"

        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.get(url, params=dict(params), headers=dict(headers))
            except RETRYABLE_TRANSPORT_ERRORS as exc:
                last_reason = type(exc).__name__
            except httpx2.RequestError as exc:
                # Not retryable: DNS failure, TLS failure, invalid URL.
                raise ProviderError(
                    f"Could not reach the {self._provider} enrichment provider "
                    f"({type(exc).__name__}). The lookup was not performed.",
                    provider=self._provider,
                ) from exc
            else:
                if response.status_code < 500:
                    return ProviderResponse(
                        status_code=response.status_code,
                        payload=self._decode(response),
                    )
                last_reason = f"http_{response.status_code}"

            if attempt == attempts:
                break

            delay = self._backoff_seconds * (2 ** (attempt - 1))
            _log.warning(
                "enrichment_request_retry",
                provider=self._provider,
                reason=last_reason,
                attempt=attempt,
                of=attempts,
                delay_seconds=delay,
            )
            await self._sleep(delay)

        raise ProviderError(
            f"The {self._provider} enrichment provider did not respond successfully after "
            f"{attempts} attempt(s) ({last_reason}). No data was retrieved and no further "
            f"attempts were made.",
            provider=self._provider,
            attempts=attempts,
        )

    def _decode(self, response: httpx2.Response) -> Mapping[str, Any]:
        """Decode a response body as a JSON object.

        Args:
            response: The provider's response.

        Returns:
            The decoded object, or an empty mapping for an empty body.

        Raises:
            ProviderError: The body was not a JSON object.
        """
        if not response.content:
            return {}
        try:
            decoded: Any = response.json()
        except ValueError as exc:
            raise ProviderError(
                f"The {self._provider} enrichment provider returned a response that was not "
                f"valid JSON. No data was retrieved.",
                provider=self._provider,
                status_code=response.status_code,
            ) from exc

        if not isinstance(decoded, dict):
            raise ProviderError(
                f"The {self._provider} enrichment provider returned an unexpected response "
                f"shape. No data was retrieved.",
                provider=self._provider,
                status_code=response.status_code,
            )
        return decoded
