"""`audit_logs`: who did what, to which record, when and from where. Append-only.

From the paused admin module design (26a99e7). Every module will record its changes here (and
security events such as failed logins), for every kind of actor; the writer comes with the admin
module. Plain reads are not recorded, except sensitive ones such as revealing a phone number.

Partitioned by month on `created_at`, so the primary key includes it. Migration 0012 creates the
monthly partitions (to December 2027) and a default one; a job must add later months before 2028.
The app's database role may only insert and read.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Index, String, Uuid, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, in_values


class ActorType(StrEnum):
    """Who did it. The first three match `auth_tokens.subject_type`."""

    USER = "user"  # a customer
    VENUE_MEMBER = "venue_member"
    ADMIN = "admin"
    DEVICE = "device"  # a venue's paired counter phone
    SYSTEM = "system"  # the CLI, background jobs, the bot acting on its own
    PUBLIC = "public"  # someone not logged in: a failed login, the application form


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(in_values("actor_type", ActorType), name="actor_type_valid"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_actor", "actor_type", "actor_id"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    # Part of the primary key because the table is partitioned on it.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now(), index=True
    )
    actor_type: Mapped[str] = mapped_column(String(14))
    # No foreign key on purpose: it points at a different table per actor_type.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(40), index=True)
    # Table name and id (or public code) of the record acted on. Empty when there is no record,
    # e.g. a failed login for an email nobody uses.
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str | None] = mapped_column(String(40))
    # The changed fields only. Never phone numbers, passwords, tokens or secrets.
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(INET)
    # Links the entry to the request's log lines (X-Request-ID).
    request_id: Mapped[str | None] = mapped_column(String(32))
