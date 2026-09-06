"""Service layer.

Orchestration between the tool layer and the ports: input normalisation, call
sequencing, cost discipline and result shaping. Services raise ``GTMError``
subclasses and import nothing from ``mcp``, which is what lets them be tested
without a protocol session.
"""

from __future__ import annotations

from gtm_mcp.services.crm import CrmService
from gtm_mcp.services.enrichment import EnrichmentService

__all__ = ["CrmService", "EnrichmentService"]
