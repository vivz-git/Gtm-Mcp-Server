"""Selection and construction of enrichment adapters from configuration.

The one place in the codebase that knows which vendors exist. Everything above
it receives the ``CompanyEnrichmentProvider`` / ``ContactEnrichmentProvider``
protocols, so adding a vendor is a new adapter plus a branch here — no service
change, no tool change.

Selecting a live provider without a credential is treated as a misconfiguration
and fails at startup. The alternative — quietly serving the offline dataset to
an operator who asked for live data — would be the kind of silent substitution
this project exists to argue against.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx2

from gtm_mcp.errors import ConfigurationError
from gtm_mcp.ports import CompanyEnrichmentProvider, ContactEnrichmentProvider
from gtm_mcp.providers.http import EnrichmentHttpClient
from gtm_mcp.providers.hunter import HunterCompanyProvider, HunterContactProvider
from gtm_mcp.providers.sample import SampleCompanyProvider, SampleContactProvider
from gtm_mcp.settings import Settings


@dataclass(frozen=True, slots=True)
class EnrichmentProviders:
    """The pair of adapters the enrichment service runs on."""

    company: CompanyEnrichmentProvider
    contact: ContactEnrichmentProvider


def build_http_client(settings: Settings) -> httpx2.AsyncClient:
    """Build the shared outbound HTTP client for enrichment calls.

    One client for the process, owned by the lifespan: connections are pooled
    across tool calls, and the timeout is applied to every request rather than
    left to a per-call argument someone will eventually forget.

    Args:
        settings: Runtime configuration.

    Returns:
        A configured async client. The caller owns closing it.
    """
    return httpx2.AsyncClient(
        timeout=httpx2.Timeout(settings.enrichment_timeout_seconds),
        limits=httpx2.Limits(max_connections=10, max_keepalive_connections=5),
        follow_redirects=False,
        headers={"User-Agent": f"{settings.server_name}/enrichment"},
    )


def build_enrichment_providers(
    settings: Settings,
    http_client: httpx2.AsyncClient | None = None,
) -> EnrichmentProviders:
    """Build the enrichment adapters named by configuration.

    Args:
        settings: Runtime configuration; ``enrichment_provider`` selects the vendor.
        http_client: Shared HTTP client. Required for any live provider.

    Returns:
        The company and contact adapters.

    Raises:
        ConfigurationError: A live provider was selected without an API key, or
            without an HTTP client to reach it with.
    """
    if settings.enrichment_provider == "sample":
        return EnrichmentProviders(
            company=SampleCompanyProvider(),
            contact=SampleContactProvider(),
        )

    if settings.enrichment_api_key is None:
        raise ConfigurationError(
            f"GTM_ENRICHMENT_PROVIDER is set to '{settings.enrichment_provider}' but "
            f"GTM_ENRICHMENT_API_KEY is not set. Provide the key, or set "
            f"GTM_ENRICHMENT_PROVIDER=sample to run against the offline dataset.",
        )
    if http_client is None:
        raise ConfigurationError(
            f"The '{settings.enrichment_provider}' enrichment provider needs an HTTP "
            f"client, and none was supplied.",
        )

    api_key = settings.enrichment_api_key.get_secret_value()
    http = EnrichmentHttpClient(
        http_client,
        provider=settings.enrichment_provider,
        max_retries=settings.enrichment_max_retries,
        backoff_seconds=settings.enrichment_retry_backoff_seconds,
    )
    return EnrichmentProviders(
        company=HunterCompanyProvider(http, api_key=api_key),
        contact=HunterContactProvider(http, api_key=api_key),
    )
