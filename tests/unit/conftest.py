"""Fixtures for provider-adapter tests.

Provider adapters are tested against a scripted HTTP transport rather than the
live API. That is not only about speed: a test suite that calls a metered
enrichment API spends real credits on every CI run, and the failure modes that
matter most here — a 429, a 500 that recovers, a truncated body — cannot be
provoked on demand against a real vendor anyway.

The stub records every outbound request, which is what lets a test assert the
property this phase actually cares about: one tool call, one provider call.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import httpx2
import pytest

from gtm_mcp.providers.http import EnrichmentHttpClient

#: A fake credential. Real keys never appear in this repository.
TEST_API_KEY = "test-api-key-not-a-real-credential"


@dataclass(slots=True)
class RecordedCall:
    """One outbound request the adapter made."""

    url: str
    params: dict[str, str]
    headers: dict[str, str]


@dataclass(slots=True)
class ProviderStub:
    """A scripted provider: replays queued responses and records every call.

    A queued entry may be an ``httpx2.Response`` to return or an exception to
    raise, which is how transport failures are simulated. Running out of queued
    entries fails the test, so an adapter that calls the provider more often
    than the test expects cannot pass quietly.
    """

    queue: list[httpx2.Response | Exception]
    calls: list[RecordedCall] = field(default_factory=list)

    @property
    def call_count(self) -> int:
        """How many outbound requests were made."""
        return len(self.calls)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        """Record the request and return the next scripted response.

        Args:
            request: The outbound request.

        Returns:
            The next queued response.

        Raises:
            AssertionError: The adapter made more calls than were scripted.
            Exception: The next queued entry is a transport failure.
        """
        self.calls.append(
            RecordedCall(
                url=str(request.url).split("?")[0],
                params=dict(request.url.params),
                headers=dict(request.headers),
            )
        )
        if not self.queue:
            raise AssertionError(
                f"the adapter made {self.call_count} provider call(s), more than the test "
                f"scripted; on a metered API every extra call is money"
            )
        nxt = self.queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def transport(self) -> httpx2.MockTransport:
        """Return a transport that serves this stub."""
        return httpx2.MockTransport(self.handle)


@dataclass(slots=True)
class SleepRecorder:
    """Captures backoff delays instead of waiting them out."""

    delays: list[float] = field(default_factory=list)

    async def __call__(self, seconds: float) -> None:
        """Record a requested delay.

        Args:
            seconds: The delay the client asked for.
        """
        self.delays.append(seconds)


@dataclass(slots=True)
class ProviderHarness:
    """A scripted provider plus the client wired to it."""

    stub: ProviderStub
    sleeps: SleepRecorder
    http: EnrichmentHttpClient
    client: httpx2.AsyncClient


@pytest.fixture
def harness() -> Callable[..., ProviderHarness]:
    """Return a factory that builds a scripted provider harness.

    Returns:
        A callable taking the scripted responses and retry configuration.
    """

    def _build(
        responses: list[httpx2.Response | Exception],
        *,
        max_retries: int = 0,
        backoff_seconds: float = 0.5,
        provider: str = "hunter",
    ) -> ProviderHarness:
        stub = ProviderStub(queue=list(responses))
        sleeps = SleepRecorder()
        client = httpx2.AsyncClient(transport=stub.transport())
        http = EnrichmentHttpClient(
            client,
            provider=provider,
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
            sleep=sleeps,
        )
        return ProviderHarness(stub=stub, sleeps=sleeps, http=http, client=client)

    return _build
