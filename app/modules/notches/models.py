"""Notchs (verified visits), the badges they earn, and the fraud signals around them.

A Notch is one customer's verified visit to one venue: they scanned the counter phone's
rotating QR and sent the message on WhatsApp within the code's lifetime. One per customer per
venue per day (in the city's timezone).
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    Base,
    CreatedAtMixin,
    Entity,
    InsertOnlyEntity,
    in_values,
    public_code_column,
)


class NotchStatus(StrEnum):
    VERIFIED = "verified"
    # Counts like a verified Notch, but waits in the proof review queue (G1).
    FLAGGED = "flagged"
    # Removed by a reviewer: counts nowhere.
    REJECTED = "rejected"


# Statuses that count: in totals, ranks, badges, venue numbers and billing.
COUNTED_STATUSES = (NotchStatus.VERIFIED, NotchStatus.FLAGGED)


class NotchFlag(StrEnum):
    """Why a Notch went to proof review."""

    # The same code came from two or more numbers (QR spec: "Same code from 2+ numbers").
    SHARED_CODE = "shared_code"
    # The counter phone had stopped checking in, so an older code was accepted (± 2 windows).
    COUNTER_OFFLINE = "counter_offline"
    # The customer tapped "Report a mistake" (C4).
    CUSTOMER_REPORT = "customer_report"


class Notch(Entity):
    __tablename__ = "notches"
    __table_args__ = (
        CheckConstraint(in_values("status", NotchStatus), name="status_valid"),
        CheckConstraint("notch_no > 0", name="notch_no_positive"),
        CheckConstraint("user_visit_no > 0", name="user_visit_no_positive"),
        CheckConstraint("local_hour BETWEEN 0 AND 23", name="local_hour_range"),
        UniqueConstraint("venue_id", "notch_no", name="uq_notches_venue_id_notch_no"),
        # One Notch per customer per venue per day. A rejected one does not block a new one.
        Index(
            "uq_notches_user_venue_day",
            "user_id",
            "venue_id",
            "local_date",
            unique=True,
            postgresql_where=text("status <> 'rejected' AND user_id IS NOT NULL"),
        ),
        Index("ix_notches_city_id_local_date", "city_id", "local_date"),
        Index("ix_notches_venue_id_scanned_at", "venue_id", "scanned_at"),
        Index("ix_notches_user_id_scanned_at", "user_id", "scanned_at"),
        Index("ix_notches_counter_id_code_window", "counter_id", "code_window"),
        # The proof review queue (G1).
        Index(
            "ix_notches_flagged",
            "scanned_at",
            postgresql_where=text("status = 'flagged'"),
        ),
    )

    # Public id, e.g. in "Report a mistake" and the admin panel (README, "IDs and public codes").
    notch_code: Mapped[str] = public_code_column("NCH")
    # NULL once the customer has deleted their data: the visit still counts for the venue.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    venue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("venues.id", ondelete="RESTRICT"))
    counter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("counter_devices.id", ondelete="SET NULL")
    )
    # Copied from the venue when the Notch is made, so rankings never need a join.
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cities.id", ondelete="RESTRICT"))
    area_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("areas.id", ondelete="SET NULL"), index=True
    )
    category: Mapped[str] = mapped_column(String(16))
    # The venue's running number: "Kahwa House · Notch #0145".
    notch_no: Mapped[int] = mapped_column(Integer)
    # This customer's nth counted Notch at this venue: "6th visit" for the owner (E6).
    user_visit_no: Mapped[int] = mapped_column(Integer)
    # When the customer sent the message (WhatsApp's timestamp), not when we processed it.
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The day and hour in the city's timezone, for "one per day", streak and time-of-day facts.
    local_date: Mapped[date] = mapped_column(Date)
    local_hour: Mapped[int] = mapped_column(SmallInteger)
    # What the customer sent, and which 30-second window it matched (offset from the scan's).
    code: Mapped[str] = mapped_column(String(8))
    code_window: Mapped[int] = mapped_column(BigInteger)
    code_offset: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(
        String(10), default=NotchStatus.VERIFIED, server_default=NotchStatus.VERIFIED.value
    )
    flags: Mapped[list[str]] = mapped_column(
        ARRAY(String(24)), default=list, server_default=text("'{}'")
    )
    # Plain words for the reviewer: "Same code from USR-… 4 s apart", or the customer's report.
    flag_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(String(200))
    # The WhatsApp message that made it.
    whatsapp_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("whatsapp_messages.id", ondelete="SET NULL")
    )


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
    notch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("notches.id", ondelete="SET NULL")
    )
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
    code: Mapped[str] = mapped_column(String(8))
    code_window: Mapped[int] = mapped_column(BigInteger)
    code_offset: Mapped[int] = mapped_column(SmallInteger)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    counter_offline: Mapped[bool] = mapped_column(Boolean)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
