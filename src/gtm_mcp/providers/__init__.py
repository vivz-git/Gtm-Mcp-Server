"""Enrichment provider adapters.

One module per vendor, each implementing the enrichment ports from
``gtm_mcp.ports``. Vendor field names, parameter names, status-code meanings and
error bodies live here and nowhere else; everything above this package sees
canonical records and ``GTMError`` subclasses.

Selection happens in :mod:`gtm_mcp.providers.factory`, driven by configuration.
"""

from __future__ import annotations

from gtm_mcp.providers.factory import (
    EnrichmentProviders,
    build_enrichment_providers,
    build_http_client,
)

__all__ = ["EnrichmentProviders", "build_enrichment_providers", "build_http_client"]
