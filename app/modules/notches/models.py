"""The badges Notchs earn, the fraud signals around them, and scans waiting for consent.

A Notch is what customers call a counted row in `visits` (app/modules/scans/models.py).
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    Base,
    CreatedAtMixin,
    Entity,
    InsertOnlyEntity,
    in_values,
)


class NotchFlag(StrEnum):
    """Why a Notch went to proof review."""

    # The same code came from two or more numbers (QR spec: "Same code from 2+ numbers").
    SHARED_CODE = "shared_code"
    # The counter phone had stopped checking in, so an older code was accepted (± 2 windows).
    COUNTER_OFFLINE = "counter_offline"
    # The customer tapped "Report a mistake" (C4).
    CUSTOMER_REPORT = "customer_report"


class Badge(StrEnum):
    """The pilot's badges (Page Inventory C8). Rules: app/modules/notches/badges.py."""

    FIRST_NOTCH = "first_notch"
    FOUNDING_MEMBER = "founding_member"
    CAFE_HOPPER = "cafe_hopper"
    NIGHT_OWL = "night_owl"
    REGULAR = "regular"


class UserBadge(InsertOnlyEntity):
    """A badge a customer has earned, once each. Never taken back."""

    __tablename__ = "user_badges"
    __table_args__ = (
        CheckConstraint(in_values("badge", Badge), name="badge_valid"),
        UniqueConstraint("user_id", "badge", name="uq_user_badges_user_id_badge"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    badge: Mapped[str] = mapped_column(String(20))
    # Where it was earned, when that means something ("First Notch · Kahwa House").
    venue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("venues.id", ondelete="SET NULL"))
    visit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("visits.id", ondelete="SET NULL"))
    earned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SignalKind(StrEnum):
    """Patterns across scans for the fraud flags page (G4)."""

    # More than 6 scans in one minute at one counter.
    MANY_SCANS_MINUTE = "many_scans_minute"
    # One code from two or more numbers.
    SHARED_CODE = "shared_code"
    # Scans accepted while the counter phone was offline.
    COUNTER_OFFLINE = "counter_offline"


class SignalStatus(StrEnum):
    OPEN = "open"
    DISMISSED = "dismissed"  # looked at, nothing wrong (e.g. friends at the counter together)
    ACTIONED = "actioned"  # looked at, something was done (Notchs rejected, venue paused...)


class FraudSignal(Entity):
    __tablename__ = "fraud_signals"
    __table_args__ = (
        CheckConstraint(in_values("kind", SignalKind), name="kind_valid"),
        CheckConstraint(in_values("status", SignalStatus), name="status_valid"),
        CheckConstraint("occurrences > 0", name="occurrences_positive"),
        Index("ix_fraud_signals_open", "last_seen_at", postgresql_where=text("status = 'open'")),
    )

    kind: Mapped[str] = mapped_column(String(24))
    # One row per pattern, e.g. "many_scans_minute:<counter>:<minute>": repeats add up.
    dedup_key: Mapped[str] = mapped_column(String(120), unique=True)
    venue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("venues.id", ondelete="CASCADE"), index=True
    )
    counter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("counter_devices.id", ondelete="SET NULL")
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    occurrences: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # Facts for the reviewer: scan counts, notch codes. Never phone numbers.
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(10), default=SignalStatus.OPEN, server_default=SignalStatus.OPEN.value
    )
    resolved_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(String(200))


class PendingCheckIn(Base, CreatedAtMixin):
    """A first scan whose code checked out, waiting for the customer to agree (B1).

    In the database, not Redis, so it is kept or used up in the same transaction as the Notch
    it becomes: a retry after a failure finds it again. One per customer (the newest scan
    wins); expired ones are deleted by the scheduler.
    """

    __tablename__ = "pending_checkins"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    venue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("venues.id", ondelete="CASCADE"))
    counter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("counter_devices.id", ondelete="SET NULL")
    )
    # The message it came from: it becomes the scan session's message once the customer agrees.
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("whatsapp_messages.id", ondelete="CASCADE")
    )
    scan_code: Mapped[str] = mapped_column(String(10))
    code: Mapped[str] = mapped_column(String(16))
    code_slot: Mapped[int] = mapped_column(BigInteger)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    counter_offline: Mapped[bool] = mapped_column(Boolean)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
