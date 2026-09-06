"""Retry, backoff and failure handling for outbound enrichment calls.

The rules under test are cost rules as much as reliability rules: a retry on a
metered API can be a second charge, so the retry predicate has to be exactly as
narrow as it claims to be.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx2
import pytest

from gtm_mcp.errors import ProviderError
from gtm_mcp.providers.http import ProviderResponse

from .conftest import ProviderHarness

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_URL = "https://api.example.test/v2/companies/find"


async def _get(harness: ProviderHarness) -> ProviderResponse:
    """Issue the request under test.

    Args:
        harness: The scripted provider harness.

    Returns:
        The decoded provider response.
    """
    return await harness.http.get_json(_URL, params={"domain": "example.com"}, headers={})


async def test_a_successful_response_is_returned_decoded(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(200, json={"data": {"domain": "example.com"}})])
    response = await built.http.get_json(_URL, params={"domain": "example.com"}, headers={})
    assert response.status_code == 200
    assert response.payload == {"data": {"domain": "example.com"}}
    assert built.stub.call_count == 1


async def test_a_transient_server_error_is_retried_and_then_succeeds(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness(
        [httpx2.Response(503, json={}), httpx2.Response(200, json={"data": {"ok": True}})],
        max_retries=2,
    )
    response = await built.http.get_json(_URL, params={}, headers={})
    assert response.status_code == 200
    assert built.stub.call_count == 2


async def test_retries_are_bounded_and_the_failure_is_reported_honestly(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(500, json={})] * 3, max_retries=2)
    with pytest.raises(ProviderError) as excinfo:
        await _get(built)

    assert built.stub.call_count == 3, "one initial attempt plus exactly two retries"
    message = str(excinfo.value)
    assert "No data was retrieved" in message
    assert "no further" in message


async def test_backoff_grows_exponentially_between_attempts(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(500, json={})] * 3, max_retries=2, backoff_seconds=0.5)
    with pytest.raises(ProviderError):
        await _get(built)
    assert built.sleeps.delays == [0.5, 1.0]


async def test_a_connection_failure_is_retried_then_surfaced_as_a_provider_error(
    harness: Callable[..., ProviderHarness],
) -> None:
    request = httpx2.Request("GET", _URL)
    built = harness(
        [httpx2.ConnectError("refused", request=request)] * 2,
        max_retries=1,
    )
    with pytest.raises(ProviderError, match="did not respond successfully"):
        await _get(built)
    assert built.stub.call_count == 2


async def test_a_read_timeout_is_retried_then_surfaced_as_a_provider_error(
    harness: Callable[..., ProviderHarness],
) -> None:
    request = httpx2.Request("GET", _URL)
    built = harness([httpx2.ReadTimeout("timed out", request=request)] * 2, max_retries=1)
    with pytest.raises(ProviderError) as excinfo:
        await _get(built)
    assert built.stub.call_count == 2
    assert "ReadTimeout" in str(excinfo.value)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 451])
async def test_no_client_error_is_ever_retried(
    harness: Callable[..., ProviderHarness], status: int
) -> None:
    """A rejection fixes nothing on a second attempt and can cost money.

    A retried 429 also turns a short rate-limit block into a longer one.
    """
    built = harness([httpx2.Response(status, json={"errors": []})], max_retries=3)
    response = await _get(built)
    assert response.status_code == status
    assert built.stub.call_count == 1
    assert built.sleeps.delays == []


async def test_retrying_is_disabled_entirely_when_configured_to_zero(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(500, json={})], max_retries=0)
    with pytest.raises(ProviderError):
        await _get(built)
    assert built.stub.call_count == 1


async def test_a_non_json_body_is_rejected_rather_than_half_parsed(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(200, text="<html>maintenance</html>")])
    with pytest.raises(ProviderError, match="not valid JSON"):
        await _get(built)


async def test_a_json_array_body_is_rejected_because_the_contract_is_an_object(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(200, json=[1, 2, 3])])
    with pytest.raises(ProviderError, match="unexpected response shape"):
        await _get(built)


async def test_an_empty_body_decodes_to_an_empty_payload(
    harness: Callable[..., ProviderHarness],
) -> None:
    built = harness([httpx2.Response(204)])
    response = await _get(built)
    assert response.payload == {}


async def test_a_non_transient_transport_failure_is_not_retried(
    harness: Callable[..., ProviderHarness],
) -> None:
    """A proxy or TLS misconfiguration fails identically on every attempt."""
    request = httpx2.Request("GET", _URL)
    built = harness([httpx2.ProxyError("bad proxy", request=request)], max_retries=3)
    with pytest.raises(ProviderError, match="Could not reach"):
        await _get(built)
    assert built.stub.call_count == 1
    assert built.sleeps.delays == []
