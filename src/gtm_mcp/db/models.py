"""SQLAlchemy ORM models for the mock CRM persistence and audit trail.

Provides the schema for:
* companies: B2B firmographic accounts keyed naturally by web domain.
* contacts: Individual prospects/leads with natural identity rules.
* lists: Named GTM segments/campaign lists.
* list_members: Membership join table linking contacts to lists.
* audit_log: Append-only ledger of write operations with safety constraints.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""


class CompanyModel(Base):
    """Firmographic record for a B2B account."""

    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    employee_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="crm")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    contacts: Mapped[list[ContactModel]] = relationship(
        "ContactModel", back_populates="company", cascade="all"
    )


class ContactModel(Base):
    """Prospect or contact in the CRM.

    Identity strategy (DECISIONS.md D-014):
    1. Normalized email is the strongest natural identity when available.
    2. (provider_name, provider_contact_id) provides identity when email is unavailable.
    3. Contacts with neither are not coalesced by name + company.
    """

    __tablename__ = "contacts"
    __table_args__ = (
        Index(
            "ix_contacts_email_unique",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
        Index(
            "ix_contacts_provider_identity_unique",
            "provider_name",
            "provider_contact_id",
            unique=True,
            postgresql_where=text("provider_name IS NOT NULL AND provider_contact_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    company_domain: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    provider_contact_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="crm")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    company: Mapped[CompanyModel | None] = relationship("CompanyModel", back_populates="contacts")
    list_memberships: Mapped[list[ListMemberModel]] = relationship(
        "ListMemberModel", back_populates="contact", cascade="all"
    )


class ListModel(Base):
    """Named GTM segment or outreach list."""

    __tablename__ = "lists"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

    members: Mapped[list[ListMemberModel]] = relationship(
        "ListMemberModel", back_populates="list", cascade="all"
    )


class ListMemberModel(Base):
    """Membership join table linking a contact to a GTM list.

    Enforces that a contact cannot appear twice in the same list.
    """

    __tablename__ = "list_members"
    __table_args__ = (
        UniqueConstraint("list_id", "contact_id", name="uq_list_members_list_contact"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    list_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lists.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    list: Mapped[ListModel] = relationship("ListModel", back_populates="members")
    contact: Mapped[ContactModel] = relationship("ContactModel", back_populates="list_memberships")


class AuditLogModel(Base):
    """Audit log entry representing an attempted write operation.

    Includes a check constraint ensuring only non-destructive operations are recorded.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint("operation IN ('upsert', 'list_add')", name="ck_audit_log_no_delete"),
    )

    audit_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True, default=_utc_now
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    operation: Mapped[str] = mapped_column(String(50), nullable=False)
    outcome: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    target_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    changed_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
