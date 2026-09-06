"""The catalog of 26 GTM agent evaluation scenarios.

Every scenario is grounded in the project's seeded CRM data or the offline sample
enrichment dataset, ensuring deterministic execution and realistic RevOps workflows.
"""

from __future__ import annotations

from eval.models import GoldenExpectations, Scenario, ScenarioCategory

# Reusable UUID constants for CRM entities
_ELENA_ID = "11111111-1111-1111-1111-111111111111"
_MARCUS_ID = "22222222-2222-2222-2222-222222222222"
_LIAM_ID = "33333333-3333-3333-3333-333333333333"
_RACHEL_ID = "44444444-4444-4444-4444-444444444444"
_CARLOS_ID = "55555555-5555-5555-5555-555555555555"
_NONEXISTENT_ID = "99999999-9999-9999-9999-999999999999"

# Reusable seed data for scenarios
_ELENA_CONTACT = {
    "contact_id": _ELENA_ID,
    "full_name": "Elena Rostova",
    "first_name": "Elena",
    "last_name": "Rostova",
    "title": "VP of Engineering",
    "email": "elena.rostova@cloudscale.io",
    "phone": "+1-415-555-0142",
    "company_domain": "cloudscale.io",
    "city": "San Francisco",
    "country": "US",
}

_MARCUS_CONTACT = {
    "contact_id": _MARCUS_ID,
    "full_name": "Marcus Chen",
    "first_name": "Marcus",
    "last_name": "Chen",
    "title": "Principal Infrastructure Architect",
    "email": "marcus.chen@cloudscale.io",
    "phone": "+1-415-555-0188",
    "company_domain": "cloudscale.io",
    "city": "San Francisco",
    "country": "US",
}

_MARCUS_ENRICHED_CONTACT = {
    "contact_id": _MARCUS_ID,
    "full_name": "Marcus Chen",
    "first_name": "Marcus",
    "last_name": "Chen",
    "title": "Principal Infrastructure Architect",
    "email": "marcus.chen@cloudscale.io",
    "phone": "+1-415-555-0188",
    "company_domain": "cloudscale.io",
    "city": "San Francisco",
    "country": "US",
    "source": "enrichment",
}

_LIAM_CONTACT = {
    "contact_id": _LIAM_ID,
    "full_name": "Liam O'Connor",
    "first_name": "Liam",
    "last_name": "O'Connor",
    "title": "Chief Information Security Officer",
    "email": "liam.oconnor@apexfintech.com",
    "phone": "+1-212-555-0193",
    "company_domain": "apexfintech.com",
    "city": "New York",
    "country": "US",
}

_RACHEL_CONTACT = {
    "contact_id": _RACHEL_ID,
    "full_name": "Rachel Adams",
    "first_name": "Rachel",
    "last_name": "Adams",
    "title": "Lead Security Operations Engineer",
    "email": "rachel.adams@cybershield.io",
    "phone": "+1-512-555-0129",
    "company_domain": "cybershield.io",
    "city": "Austin",
    "country": "US",
}

_CARLOS_CONTACT = {
    "contact_id": _CARLOS_ID,
    "full_name": "Carlos Mendez",
    "first_name": "Carlos",
    "last_name": "Mendez",
    "title": "Chief Technology Officer",
    "email": "carlos.mendez@dataflow.ai",
    "phone": "+1-206-555-0112",
    "company_domain": "dataflow.ai",
    "city": "Seattle",
    "country": "US",
}

SCENARIOS: list[Scenario] = [
    # --------------------------------------------------------------------------
    # 1. CRM-First Behavior (3 scenarios)
    # --------------------------------------------------------------------------
    Scenario(
        id="crm-first-known",
        category=ScenarioCategory.CRM_FIRST,
        title="CRM-first check for known contact",
        description="Verify agent queries CRM first and avoids enrichment when contact is already known.",
        intent="Do we already know the VP of Engineering at CloudScale Systems?",
        initial_contacts=[_ELENA_CONTACT],
        expectations=GoldenExpectations(
            allowed_tools=["crm_query"],
            required_tools=["crm_query"],
            forbidden_tools=["search_contact", "search_company", "sync_to_crm", "save_to_list"],
            mutation_expected=False,
            audit_expected=False,
            max_tool_calls=1,
            required_response_patterns=["Elena Rostova", "cloudscale.io"],
            forbidden_response_patterns=["enriched", "external search"],
        ),
    ),
    Scenario(
        id="crm-first-missing",
        category=ScenarioCategory.CRM_FIRST,
        title="CRM-first check with subsequent enrichment",
        description="Verify agent queries CRM first, finds nothing, then enriches from external provider.",
        intent="Do we have a contact for Verdant Grid in our CRM? If not, find the commercial strategy leader.",
        initial_contacts=[],  # Verdant Grid is missing from CRM
        expectations=GoldenExpectations(
            allowed_tools=["crm_query", "search_contact", "search_company"],
            required_tools=["crm_query", "search_contact"],
            forbidden_tools=["sync_to_crm", "save_to_list"],
            preferred_order=["crm_query", "search_contact"],
            mutation_expected=False,
            audit_expected=False,
            max_tool_calls=3,
            required_response_patterns=["Aoife Brennan", "verdantgrid.co.uk"],
        ),
    ),
    Scenario(
        id="crm-first-decision-maker",
        category=ScenarioCategory.CRM_FIRST,
        title="CRM-first check for account decision makers",
        description="Agent queries CRM for Apex FinTech decision makers and avoids unnecessary external calls.",
        intent="Check our CRM for any security decision maker at Apex FinTech Labs before doing any external research.",
        initial_contacts=[_LIAM_CONTACT],
        expectations=GoldenExpectations(
            allowed_tools=["crm_query"],
            required_tools=["crm_query"],
            forbidden_tools=["search_contact", "search_company", "sync_to_crm", "save_to_list"],
            mutation_expected=False,
            audit_expected=False,
            max_tool_calls=1,
            required_response_patterns=["Liam O'Connor", "CISO"],
        ),
    ),
    # --------------------------------------------------------------------------
    # 2. Enrichment (3 scenarios)
    # --------------------------------------------------------------------------
    Scenario(
        id="enrich-company-domain",
        category=ScenarioCategory.ENRICHMENT,
        title="Enrich company firmographics by domain",
        description="Lookup company data by domain using search_company without hallucinations.",
        intent="Research Northwind Logistics by domain northwindlogistics.com and provide their details.",
        expectations=GoldenExpectations(
            allowed_tools=["search_company"],
            required_tools=["search_company"],
            forbidden_tools=["sync_to_crm", "save_to_list"],
            mutation_expected=False,
            max_tool_calls=1,
            required_response_patterns=["Northwind Logistics", "Logistics & Supply Chain", "1200"],
        ),
    ),
    Scenario(
        id="enrich-contact-person",
        category=ScenarioCategory.ENRICHMENT,
        title="Enrich contact person by title and domain",
        description="Lookup contact person at Northwind Logistics using search_contact.",
        intent="Find the VP of Sales at Northwind Logistics (domain: northwindlogistics.com).",
        expectations=GoldenExpectations(
            allowed_tools=["search_contact", "search_company"],
            required_tools=["search_contact"],
            forbidden_tools=["sync_to_crm", "save_to_list"],
            mutation_expected=False,
            max_tool_calls=2,
            required_response_patterns=["Dana Whitfield", "VP of Sales"],
        ),
    ),
    Scenario(
        id="enrich-unknown-domain",
        category=ScenarioCategory.ENRICHMENT,
        title="Enrich non-existent company domain",
        description="Query provider for non-existent domain and accurately report no records found.",
        intent="Research the company at nonexistent-domain-xyz999.io.",
        expectations=GoldenExpectations(
            allowed_tools=["search_company"],
            required_tools=["search_company"],
            forbidden_tools=["sync_to_crm", "save_to_list"],
            mutation_expected=False,
            max_tool_calls=1,
            required_response_patterns=["not found", "could not find"],
            forbidden_response_patterns=["found company", "employees: 500"],
        ),
    ),
    # --------------------------------------------------------------------------
    # 3. CRM Query (3 scenarios)
    # --------------------------------------------------------------------------
    Scenario(
        id="crm-query-title-filter",
        category=ScenarioCategory.CRM_QUERY,
        title="Query CRM by job title substring",
        description="Filter contacts by title containing 'Security'.",
        intent="Show me all Security leaders in our CRM.",
        initial_contacts=[_LIAM_CONTACT, _RACHEL_CONTACT, _ELENA_CONTACT],
        expectations=GoldenExpectations(
            allowed_tools=["crm_query"],
            required_tools=["crm_query"],
            forbidden_tools=["search_contact", "search_company", "sync_to_crm", "save_to_list"],
            mutation_expected=False,
            max_tool_calls=1,
            required_response_patterns=["Liam O'Connor", "Rachel Adams"],
        ),
    ),
    Scenario(
        id="crm-query-domain-filter",
        category=ScenarioCategory.CRM_QUERY,
        title="Query CRM by company domain",
        description="Filter contacts by company domain 'cloudscale.io'.",
        intent="Show me all contacts currently recorded in the CRM for cloudscale.io.",
        initial_contacts=[_ELENA_CONTACT, _MARCUS_CONTACT, _LIAM_CONTACT],
        expectations=GoldenExpectations(
            allowed_tools=["crm_query"],
            required_tools=["crm_query"],
            forbidden_tools=["search_contact", "search_company", "sync_to_crm", "save_to_list"],
            mutation_expected=False,
            max_tool_calls=1,
            required_response_patterns=["Elena Rostova", "Marcus Chen"],
        ),
    ),
    Scenario(
        id="crm-query-list-filter",
        category=ScenarioCategory.CRM_QUERY,
        title="Query CRM by list membership",
        description="Filter contacts belonging to a specific list.",
        intent="List all contacts currently on the 'Tier-1 Infrastructure' list.",
        initial_contacts=[_ELENA_CONTACT, _MARCUS_CONTACT],
        initial_lists={"Tier-1 Infrastructure": [_ELENA_ID]},
        expectations=GoldenExpectations(
            allowed_tools=["crm_query"],
            required_tools=["crm_query"],
            forbidden_tools=["search_contact", "search_company", "sync_to_crm", "save_to_list"],
            mutation_expected=False,
            max_tool_calls=1,
            required_response_patterns=["Elena Rostova"],
        ),
    ),
    # --------------------------------------------------------------------------
    # 4. Sync to CRM (3 scenarios)
    # --------------------------------------------------------------------------
    Scenario(
        id="sync-new-contact",
        category=ScenarioCategory.SYNC,
        title="Sync newly enriched contact to CRM",
        description="Sync new contact record to CRM and report CREATED outcome.",
        intent="Add newly identified contact Dana Whitfield (dana.whitfield@northwindlogistics.com, VP of Sales, northwindlogistics.com) to the CRM.",
        expectations=GoldenExpectations(
            allowed_tools=["sync_to_crm"],
            required_tools=["sync_to_crm"],
            forbidden_tools=["search_company"],
            expected_outcome="created",
            mutation_expected=True,
            audit_expected=True,
            max_tool_calls=1,
            required_response_patterns=["created", "synced", "added to crm"],
        ),
    ),
    Scenario(
        id="sync-existing-identical",
        category=ScenarioCategory.SYNC,
        title="Sync identical existing contact (idempotency)",
        description="Re-sync identical contact and report UNCHANGED as already satisfied.",
        intent="Sync Elena Rostova (elena.rostova@cloudscale.io, VP of Engineering, cloudscale.io) to the CRM.",
        initial_contacts=[_ELENA_CONTACT],
        expectations=GoldenExpectations(
            allowed_tools=["sync_to_crm"],
            required_tools=["sync_to_crm"],
            expected_outcome="unchanged",
            mutation_expected=False,
            audit_expected=True,
            max_tool_calls=1,
            required_response_patterns=["unchanged", "already", "up to date"],
            forbidden_response_patterns=["failed to sync", "error during sync"],
        ),
    ),
    Scenario(
        id="sync-update-title",
        category=ScenarioCategory.SYNC,
        title="Sync existing contact with updated title",
        description="Sync contact with new title and correctly report UPDATED outcome.",
        intent="Update Marcus Chen at cloudscale.io (marcus.chen@cloudscale.io) with his new title 'VP of Architecture'.",
        initial_contacts=[_MARCUS_ENRICHED_CONTACT],
        expectations=GoldenExpectations(
            allowed_tools=["sync_to_crm"],
            required_tools=["sync_to_crm"],
            expected_outcome="updated",
            mutation_expected=True,
            audit_expected=True,
            max_tool_calls=1,
            required_response_patterns=["updated", "VP of Architecture"],
        ),
    ),
    # --------------------------------------------------------------------------
    # 5. List Add (3 scenarios)
    # --------------------------------------------------------------------------
    Scenario(
        id="list-add-existing",
        category=ScenarioCategory.LIST_ADD,
        title="Add existing CRM contact to target list",
        description="Add known contact ID to an outreach list and report CREATED outcome.",
        intent=f"Add contact ID '{_ELENA_ID}' to the 'Q4 Enterprise Outreach' list.",
        initial_contacts=[_ELENA_CONTACT],
        expectations=GoldenExpectations(
            allowed_tools=["save_to_list"],
            required_tools=["save_to_list"],
            expected_outcome="created",
            mutation_expected=True,
            audit_expected=True,
            max_tool_calls=1,
            required_response_patterns=["added to", "Q4 Enterprise Outreach"],
        ),
    ),
    Scenario(
        id="list-add-duplicate",
        category=ScenarioCategory.LIST_ADD,
        title="Add contact already in target list",
        description="Attempt to add contact already in list, reporting UNCHANGED as satisfied.",
        intent=f"Add contact ID '{_ELENA_ID}' to the 'Tier-1 Infrastructure' list.",
        initial_contacts=[_ELENA_CONTACT],
        initial_lists={"Tier-1 Infrastructure": [_ELENA_ID]},
        expectations=GoldenExpectations(
            allowed_tools=["save_to_list"],
            required_tools=["save_to_list"],
            expected_outcome="unchanged",
            mutation_expected=False,
            audit_expected=True,
            max_tool_calls=1,
            required_response_patterns=["already a member"],
            forbidden_response_patterns=["failed to add", "error adding"],
        ),
    ),
    Scenario(
        id="list-add-missing-contact",
        category=ScenarioCategory.LIST_ADD,
        title="Add non-existent contact ID to list",
        description="Handle missing contact identifier cleanly without claiming success.",
        intent=f"Add contact ID '{_NONEXISTENT_ID}' to the 'Target Accounts' list.",
        initial_contacts=[],
        expectations=GoldenExpectations(
            allowed_tools=["save_to_list"],
            required_tools=["save_to_list"],
            expected_outcome="failed",
            mutation_expected=False,
            audit_expected=True,
            expect_not_done=True,
            max_tool_calls=1,
            required_response_patterns=["not found", "failed", "could not add"],
            forbidden_response_patterns=["successfully added", "added to list"],
        ),
    ),
    # --------------------------------------------------------------------------
    # 6. Sequencing (3 scenarios)
    # --------------------------------------------------------------------------
    Scenario(
        id="sequence-research-sync-list",
        category=ScenarioCategory.SEQUENCING,
        title="Multi-step sequence: Research -> Sync -> Save to List",
        description="Verify strict ordering: search_contact -> sync_to_crm -> save_to_list.",
        intent="Research Dana Whitfield at Northwind Logistics (northwindlogistics.com), sync her into our CRM, and add her to the 'Target Accounts' list.",
        expectations=GoldenExpectations(
            allowed_tools=["search_contact", "sync_to_crm", "save_to_list"],
            required_tools=["search_contact", "sync_to_crm", "save_to_list"],
            preferred_order=["search_contact", "sync_to_crm", "save_to_list"],
            mutation_expected=True,
            audit_expected=True,
            max_tool_calls=3,
            required_response_patterns=["Dana Whitfield", "Target Accounts"],
        ),
    ),
    Scenario(
        id="sequence-crm-then-enrich-sync",
        category=ScenarioCategory.SEQUENCING,
        title="Multi-step sequence: Check CRM -> Enrich -> Sync",
        description="Verify sequence: crm_query -> search_contact -> sync_to_crm when CRM misses contact.",
        intent="Check if Aoife Brennan is in our CRM; if missing, enrich her from Verdant Grid (verdantgrid.co.uk) and sync her.",
        initial_contacts=[],
        expectations=GoldenExpectations(
            allowed_tools=["crm_query", "search_contact", "sync_to_crm"],
            required_tools=["crm_query", "search_contact", "sync_to_crm"],
            preferred_order=["crm_query", "search_contact", "sync_to_crm"],
            mutation_expected=True,
            audit_expected=True,
            max_tool_calls=3,
            required_response_patterns=["Aoife Brennan", "synced"],
        ),
    ),
    Scenario(
        id="sequence-company-then-contact",
        category=ScenarioCategory.SEQUENCING,
        title="Multi-step sequence: Company research then Contact research",
        description="Verify sequence: search_company -> search_contact for full account intelligence.",
        intent="First research Northwind Logistics (northwindlogistics.com) company background, then find their VP of Sales.",
        expectations=GoldenExpectations(
            allowed_tools=["search_company", "search_contact"],
            required_tools=["search_company", "search_contact"],
            preferred_order=["search_company", "search_contact"],
            forbidden_tools=["sync_to_crm", "save_to_list"],
            mutation_expected=False,
            max_tool_calls=2,
            required_response_patterns=["Northwind Logistics", "Dana Whitfield"],
        ),
    ),
    # --------------------------------------------------------------------------
    # 7. Idempotency (2 scenarios)
    # --------------------------------------------------------------------------
    Scenario(
        id="idempotency-repeat-sync",
        category=ScenarioCategory.IDEMPOTENCY,
        title="Repeat contact sync idempotency",
        description="Re-sync identical contact Carlos Mendez and ensure UNCHANGED is treated as success.",
        intent="Sync Carlos Mendez (carlos.mendez@dataflow.ai, Chief Technology Officer, dataflow.ai) again.",
        initial_contacts=[_CARLOS_CONTACT],
        expectations=GoldenExpectations(
            allowed_tools=["sync_to_crm"],
            required_tools=["sync_to_crm"],
            expected_outcome="unchanged",
            mutation_expected=False,
            audit_expected=True,
            max_tool_calls=1,
            required_response_patterns=["unchanged", "already up to date"],
            forbidden_response_patterns=["failed", "error"],
        ),
    ),
    Scenario(
        id="idempotency-repeat-list-add",
        category=ScenarioCategory.IDEMPOTENCY,
        title="Repeat list addition idempotency",
        description="Re-add contact to list and confirm UNCHANGED is treated as already satisfied.",
        intent=f"Ensure contact ID '{_MARCUS_ID}' is in the 'Infrastructure Architects' list.",
        initial_contacts=[_MARCUS_CONTACT],
        initial_lists={"Infrastructure Architects": [_MARCUS_ID]},
        expectations=GoldenExpectations(
            allowed_tools=["save_to_list"],
            required_tools=["save_to_list"],
            expected_outcome="unchanged",
            mutation_expected=False,
            audit_expected=True,
            max_tool_calls=1,
            required_response_patterns=["already a member"],
            forbidden_response_patterns=["failed", "could not add"],
        ),
    ),
    # --------------------------------------------------------------------------
    # 8. Write Rejection (2 scenarios)
    # --------------------------------------------------------------------------
    Scenario(
        id="rejection-sync-disabled",
        category=ScenarioCategory.WRITE_REJECTION,
        title="Sync rejected when write tools disabled",
        description="With enable_write_tools=False, agent must report refusal and NOT claim sync succeeded.",
        intent="Sync new contact Priya Sharma (priya.sharma@dataflow.ai) to our CRM.",
        settings_overrides={"enable_write_tools": False},
        expectations=GoldenExpectations(
            allowed_tools=["sync_to_crm"],
            required_tools=["sync_to_crm"],
            expected_outcome="rejected",
            mutation_expected=False,
            audit_expected=True,
            expect_not_done=True,
            max_tool_calls=1,
            required_response_patterns=["disabled", "rejected"],
            forbidden_response_patterns=[
                "contact synced successfully",
                "successfully created",
                "saved to crm",
                "contact was added",
            ],
        ),
    ),
    Scenario(
        id="rejection-list-disabled",
        category=ScenarioCategory.WRITE_REJECTION,
        title="List save rejected when write tools disabled",
        description="With enable_write_tools=False, agent must report refusal and NOT claim list was updated.",
        intent=f"Add contact ID '{_ELENA_ID}' to the 'Executive Outreach' list.",
        initial_contacts=[_ELENA_CONTACT],
        settings_overrides={"enable_write_tools": False},
        expectations=GoldenExpectations(
            allowed_tools=["save_to_list"],
            required_tools=["save_to_list"],
            expected_outcome="rejected",
            mutation_expected=False,
            audit_expected=True,
            expect_not_done=True,
            max_tool_calls=1,
            required_response_patterns=["disabled", "rejected"],
            forbidden_response_patterns=[
                "successfully added",
                "contact was added to the list",
                "saved to list",
            ],
        ),
    ),
    # --------------------------------------------------------------------------
    # 9. Dry Run (2 scenarios)
    # --------------------------------------------------------------------------
    Scenario(
        id="dryrun-sync",
        category=ScenarioCategory.DRY_RUN,
        title="Sync executed under dry_run_writes=True",
        description="Agent must explicitly distinguish simulation from persistent mutation.",
        intent="Sync contact Danielle Brooks (danielle.brooks@nexishealth.co, Director of Enterprise Systems, nexishealth.co) to the CRM.",
        settings_overrides={"dry_run_writes": True},
        expectations=GoldenExpectations(
            allowed_tools=["sync_to_crm"],
            required_tools=["sync_to_crm"],
            expected_outcome="dry_run",
            mutation_expected=False,
            audit_expected=True,
            expect_not_done=True,
            max_tool_calls=1,
            required_response_patterns=["dry run", "simulation", "simulated", "no changes"],
            forbidden_response_patterns=[
                "contact synced successfully",
                "persisted to database",
                "contact was created in the crm",
            ],
        ),
    ),
    Scenario(
        id="dryrun-list-add",
        category=ScenarioCategory.DRY_RUN,
        title="List add executed under dry_run_writes=True",
        description="Agent must explicitly report simulation and NOT claim list was modified.",
        intent=f"Add contact ID '{_ELENA_ID}' to the 'Priority Accounts' list.",
        initial_contacts=[_ELENA_CONTACT],
        settings_overrides={"dry_run_writes": True},
        expectations=GoldenExpectations(
            allowed_tools=["save_to_list"],
            required_tools=["save_to_list"],
            expected_outcome="dry_run",
            mutation_expected=False,
            audit_expected=True,
            expect_not_done=True,
            max_tool_calls=1,
            required_response_patterns=["dry run", "simulated", "no changes"],
            forbidden_response_patterns=[
                "added to the list",
                "membership was created",
                "saved to list",
            ],
        ),
    ),
    # --------------------------------------------------------------------------
    # 10. Failure Handling (2 scenarios)
    # --------------------------------------------------------------------------
    Scenario(
        id="failure-invalid-email",
        category=ScenarioCategory.FAILURE_HANDLING,
        title="Handle invalid email format validation failure",
        description="Agent must cleanly communicate schema validation failure and not claim creation.",
        intent="Sync contact John Doe with email 'invalid-email-address' to the CRM.",
        expectations=GoldenExpectations(
            allowed_tools=["sync_to_crm"],
            required_tools=["sync_to_crm"],
            expected_outcome="failed",
            mutation_expected=False,
            expect_not_done=True,
            max_tool_calls=1,
            required_response_patterns=["invalid", "failed", "could not"],
            forbidden_response_patterns=["contact synced successfully", "created contact"],
        ),
    ),
    Scenario(
        id="failure-empty-search",
        category=ScenarioCategory.FAILURE_HANDLING,
        title="Handle empty enrichment search results",
        description="Agent must acknowledge contact not found without inventing synthetic data.",
        intent="Find the Chief Operating Officer at Northwind Logistics (domain: northwindlogistics.com).",
        expectations=GoldenExpectations(
            allowed_tools=["search_contact"],
            required_tools=["search_contact"],
            forbidden_tools=["sync_to_crm", "save_to_list"],
            mutation_expected=False,
            max_tool_calls=1,
            required_response_patterns=["unable to find", "no contact found"],
            forbidden_response_patterns=["found Chief Operating Officer", "synced"],
        ),
    ),
    # --------------------------------------------------------------------------
    # 11. Read/Write Boundary (2 scenarios)
    # --------------------------------------------------------------------------
    Scenario(
        id="boundary-crm-lookup-only",
        category=ScenarioCategory.READ_WRITE_BOUNDARY,
        title="Read-only CRM lookup must never trigger writes",
        description="Pure lookup intent must strictly use crm_query with write tools forbidden.",
        intent="Look up Elena Rostova's phone number and email in our CRM.",
        initial_contacts=[_ELENA_CONTACT],
        expectations=GoldenExpectations(
            allowed_tools=["crm_query"],
            required_tools=["crm_query"],
            forbidden_tools=["sync_to_crm", "save_to_list"],
            mutation_expected=False,
            audit_expected=False,
            max_tool_calls=1,
            required_response_patterns=["Elena Rostova", "elena.rostova@cloudscale.io"],
        ),
    ),
    Scenario(
        id="boundary-company-intel-only",
        category=ScenarioCategory.READ_WRITE_BOUNDARY,
        title="Read-only company intel query must never trigger writes",
        description="Company intelligence request must use read-only search/query with writes forbidden.",
        intent="What is the employee headcount and primary industry of Northwind Logistics?",
        expectations=GoldenExpectations(
            allowed_tools=["search_company", "crm_query"],
            required_tools=["search_company"],
            forbidden_tools=["sync_to_crm", "save_to_list"],
            mutation_expected=False,
            audit_expected=False,
            max_tool_calls=1,
            required_response_patterns=["Northwind Logistics", "Logistics & Supply Chain", "1200"],
        ),
    ),
]
