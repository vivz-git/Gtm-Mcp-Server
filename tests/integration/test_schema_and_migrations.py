"""Integration tests verifying database schema and Alembic migration application."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_all_tables_exist_with_expected_columns(pg_engine: AsyncEngine) -> None:
    """Alembic migrations must establish all five target tables."""
    async with pg_engine.connect() as conn:
        tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        assert "companies" in tables
        assert "contacts" in tables
        assert "lists" in tables
        assert "list_members" in tables
        assert "audit_log" in tables

        # Inspect columns for companies
        company_cols = await conn.run_sync(
            lambda sync_conn: [c["name"] for c in inspect(sync_conn).get_columns("companies")]
        )
        assert {
            "id",
            "domain",
            "name",
            "industry",
            "employee_count",
            "country",
            "created_at",
        }.issubset(company_cols)

        # Inspect columns for contacts
        contact_cols = await conn.run_sync(
            lambda sync_conn: [c["name"] for c in inspect(sync_conn).get_columns("contacts")]
        )
        assert {
            "id",
            "company_id",
            "company_domain",
            "full_name",
            "email",
            "title",
            "provider_name",
            "provider_contact_id",
            "created_at",
        }.issubset(contact_cols)


async def test_audit_log_check_constraint_rejects_delete_operation(pg_engine: AsyncEngine) -> None:
    """Database check constraint must reject any forbidden destructive operations."""
    async with pg_engine.connect() as conn:
        with pytest.raises(Exception, match="ck_audit_log_no_delete"):
            await conn.execute(
                text(
                    """
                    INSERT INTO audit_log (
                        audit_id, occurred_at, tool_name, operation, outcome,
                        target_type, changed_fields, dry_run
                    ) VALUES (
                        'test-audit-del-1', NOW(), 'delete_tool', 'delete', 'failed',
                        'contact', '[]'::jsonb, false
                    )
                    """
                )
            )


async def test_list_members_enforces_unique_contact_per_list(pg_engine: AsyncEngine) -> None:
    """Duplicate membership for the same contact in a list must fail at database level."""
    async with pg_engine.begin() as conn:
        # Create dummy list and contact
        list_res = await conn.execute(
            text(
                "INSERT INTO lists (id, name, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'Constraint Test List', NOW(), NOW()) RETURNING id"
            )
        )
        list_id = list_res.scalar_one()

        contact_res = await conn.execute(
            text(
                "INSERT INTO contacts (id, full_name, email, source, confidence, "
                "created_at, updated_at) VALUES (gen_random_uuid(), 'Constraint User', "
                "'const_user@example.com', 'crm', 1.0, NOW(), NOW()) RETURNING id"
            )
        )
        contact_id = contact_res.scalar_one()

        # Insert first membership
        await conn.execute(
            text(
                "INSERT INTO list_members (id, list_id, contact_id, added_at) "
                "VALUES (gen_random_uuid(), :lid, :cid, NOW())"
            ),
            {"lid": list_id, "cid": contact_id},
        )

        # Duplicate insertion must violate unique constraint
        with pytest.raises(Exception, match="uq_list_members_list_contact"):
            await conn.execute(
                text(
                    "INSERT INTO list_members (id, list_id, contact_id, added_at) "
                    "VALUES (gen_random_uuid(), :lid, :cid, NOW())"
                ),
                {"lid": list_id, "cid": contact_id},
            )
