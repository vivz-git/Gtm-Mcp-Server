"""Integration tests for reproducible CRM seeding."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gtm_mcp.crm.seed import seed_database
from gtm_mcp.db.models import CompanyModel, ContactModel, ListMemberModel, ListModel

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_seed_database_is_reproducible_and_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Seeding populates mock CRM data and running again creates no duplicates."""
    # First seed run
    stats1 = await seed_database(session_factory)
    assert stats1.companies_created >= 0  # May have been seeded in previous test run
    assert stats1.contacts_created >= 0

    # Verify counts in database
    async with session_factory() as session:
        comp_count = (await session.execute(select(func.count(CompanyModel.id)))).scalar_one()
        cont_count = (await session.execute(select(func.count(ContactModel.id)))).scalar_one()
        list_count = (await session.execute(select(func.count(ListModel.id)))).scalar_one()
        member_count = (await session.execute(select(func.count(ListMemberModel.id)))).scalar_one()

        assert comp_count >= 6
        assert cont_count >= 14
        assert list_count >= 3
        assert member_count >= 11

    # Second seed run must create ZERO new records
    stats2 = await seed_database(session_factory)
    assert stats2.companies_created == 0
    assert stats2.contacts_created == 0
    assert stats2.lists_created == 0
    assert stats2.memberships_created == 0
