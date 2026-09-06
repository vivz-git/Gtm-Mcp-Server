"""Command-line script to populate the mock CRM with reproducible demo data."""

import asyncio

from gtm_mcp.crm.seed import seed_database
from gtm_mcp.db.engine import build_engine, build_session_factory
from gtm_mcp.settings import Settings


async def main() -> None:
    """Execute the database seed against the configured database."""
    settings = Settings()
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    try:
        stats = await seed_database(session_factory)
        print(
            f"Seed complete: {stats.companies_created} companies created "
            f"({stats.companies_updated} updated), {stats.contacts_created} contacts created "
            f"({stats.contacts_updated} updated), {stats.lists_created} lists created, "
            f"{stats.memberships_created} memberships created."
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
