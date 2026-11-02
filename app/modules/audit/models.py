"""The audit log (G6): who changed what, kept apart from the application logs.

Rows are only ever added. They name people and things by label (an admin's name, a venue's
name, a customer's USR- code), never by phone number.
"""

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import InsertOnlyEntity, in_values


class ActorType(StrEnum):
    ADMIN = "admin"
    VENUE_MEMBER = "venue_member"
    USER = "user"
    SYSTEM = "system"  # jobs and the WhatsApp bot


class AuditLog(InsertOnlyEntity):
    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(in_values("actor_type", ActorType), name="actor_type_valid"),
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_target", "target_type", "target_id"),
    )

    actor_type: Mapped[str] = mapped_column(String(14))
    actor_id: Mapped[uuid.UUID | None] = mapped_column()
    # e.g. "Harsh", "System". Kept as text so the row still reads well if the actor is gone.
    actor_label: Mapped[str] = mapped_column(String(80))
    # Dotted verb, e.g. "notch.rejected", "venue.paused", "user.deleted".
    action: Mapped[str] = mapped_column(String(40))
    target_type: Mapped[str] = mapped_column(String(24))
    target_id: Mapped[uuid.UUID | None] = mapped_column()
    # e.g. "NCH-7K3M9QX2 · Kahwa House", "USR-…", "Riverside Roasters".
    target_label: Mapped[str] = mapped_column(String(160))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
