"""Scan sessions, the visits they become, and the points visits earn (spec sheet tables).

    scan_sessions   every scan (one per inbound WhatsApp message): verified or rejected, and why
    visits          one verified Notch: a customer at a venue on an IST day (one per day)
    points_ledger   every point given, with the reason; the source of truth for ranks

whatsapp message -> scan_session -> visit (visits.scan_session_id). Proofs (bill photos, later)
attach to the scan session.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity, InsertOnlyEntity, in_values, public_code_column


class VisitStatus(StrEnum):
    VALID = "valid"
    # Counts like a valid visit, but waits in the proof review queue (G1).
    FLAGGED = "flagged"
    REVOKED = "revoked"  # found to be fraud: no longer counts


# Statuses that count: in totals, ranks, badges, venue numbers and billing.
COUNTED_STATUSES = (VisitStatus.VALID, VisitStatus.FLAGGED)


class VerificationLevel(StrEnum):
    BASIC = "basic"  # the live code from the counter phone
    VERIFIED = "verified"  # a bill was checked too (open decision, not used yet)


class PointsReason(StrEnum):
    VISIT = "visit"  # +10, every counted visit
    NEW_VENUE = "new_venue"  # +2, the first visit to a venue
    EVERY_THIRD_VISIT = "every_third_visit"  # +2, the 3rd, 6th, 9th... visit to the same venue
    ADJUSTMENT = "adjustment"  # an admin's correction (may be negative)


class ScanStatus(StrEnum):
    VERIFIED = "verified"  # became a visit
    REJECTED = "rejected"  # see reject_reason


class RejectReason(StrEnum):
    UNKNOWN_VENUE = "unknown_venue"  # no venue has this scan code
    VENUE_UNAVAILABLE = "venue_unavailable"  # not live (paused, closed, no counter phone)
    WRONG_CODE = "wrong_code"
    EXPIRED_CODE = "expired_code"  # a real code, but too old
    CODE_USED = "code_used"  # this code was used by too many people
    ALREADY_TODAY = "already_today"  # one stamp per venue per day
    DAILY_LIMIT = "daily_limit"  # too many venues today
    BLOCKED_CUSTOMER = "blocked_customer"


class ScanSession(InsertOnlyEntity):
    """One scan: the pre-typed message a customer sent from a venue's QR. Never holds a phone
    number. Checked in one go, so it is verified or rejected right away (no `received` state)."""

    __tablename__ = "scan_sessions"
    __table_args__ = (
        CheckConstraint(in_values("status", ScanStatus), name="status_valid"),
        CheckConstraint(in_values("reject_reason", RejectReason), name="reject_reason_valid"),
        # A reason exactly when rejected.
        CheckConstraint(
            "(status = 'rejected') = (reject_reason IS NOT NULL)",
            name="reject_reason_when_rejected",
        ),
        # Insights (busy hours) and abuse checks read by venue or customer and time.
        Index("ix_scan_sessions_venue_created_at", "venue_id", "created_at"),
        Index("ix_scan_sessions_user_created_at", "user_id", "created_at"),
    )

    # NULL once the customer has deleted their data.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Null when the code matched no venue.
    venue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("venues.id", ondelete="RESTRICT"))
    # Which counter phone showed the code.
    counter_device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("counter_devices.id", ondelete="SET NULL")
    )
    # The message that started it: one scan session per message (Meta redeliveries included).
    # NULL once the customer's messages have been deleted with their data.
    inbound_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("whatsapp_messages.id", ondelete="SET NULL"), unique=True
    )
    # What the customer sent, uppercased (may not be a real venue's code).
    scan_code: Mapped[str | None] = mapped_column(String(10))
    # The live code they sent (6 digits).
    code_submitted: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(10), index=True)
    reject_reason: Mapped[str | None] = mapped_column(String(20))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Visit(Entity):
    __tablename__ = "visits"
    __table_args__ = (
        # One counted visit per customer per venue per IST day: the final guard when two
        # messages arrive at once.
        UniqueConstraint("user_id", "venue_id", "visit_date", name="uq_visits_user_venue_day"),
        UniqueConstraint("venue_id", "notch_no", name="uq_visits_venue_notch_no"),
        CheckConstraint(in_values("status", VisitStatus), name="status_valid"),
        CheckConstraint(
            in_values("verification_level", VerificationLevel), name="verification_level_valid"
        ),
        CheckConstraint("notch_no > 0", name="notch_no_positive"),
        CheckConstraint("visit_no_at_venue > 0", name="visit_no_at_venue_positive"),
        CheckConstraint("local_hour BETWEEN 0 AND 23", name="local_hour_range"),
        Index("ix_visits_venue_visited_at", "venue_id", "visited_at"),
        Index("ix_visits_user_visited_at", "user_id", "visited_at"),
        Index("ix_visits_city_id_visit_date", "city_id", "visit_date"),
        Index("ix_visits_counter_id_code_slot", "counter_id", "code_slot"),
        # The proof review queue (G1).
        Index("ix_visits_flagged", "visited_at", postgresql_where=text("status = 'flagged'")),
    )

    # Public id, e.g. in "Report a mistake" and the admin panel (README, "IDs and public codes").
    notch_code: Mapped[str] = public_code_column("NCH")
    # NULL once the customer has deleted their data: the visit still counts for the venue.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    venue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("venues.id", ondelete="RESTRICT"))
    counter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("counter_devices.id", ondelete="SET NULL")
    )
    # Copied from the venue when the visit is made, so rankings never need a join.
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cities.id", ondelete="RESTRICT"))
    area_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("areas.id", ondelete="SET NULL"), index=True
    )
    category: Mapped[str] = mapped_column(String(16))
    # The verified scan this visit came from; one visit per scan session.
    scan_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scan_sessions.id", ondelete="RESTRICT"), unique=True
    )
    # The day in India (Asia/Kolkata), where "one per day" is counted.
    visit_date: Mapped[date] = mapped_column(Date)
    # The hour there, for time-of-day facts.
    local_hour: Mapped[int] = mapped_column(SmallInteger)
    visited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The venue's running number: "Notch #0145".
    notch_no: Mapped[int] = mapped_column(Integer)
    # This customer's 1st, 2nd... visit here (the venue sees this, never the phone number).
    visit_no_at_venue: Mapped[int] = mapped_column(Integer)
    # The live code's time slot (TOTP), to stop one code being used by many people.
    code_slot: Mapped[int] = mapped_column(BigInteger)
    code: Mapped[str] = mapped_column(String(16))
    verification_level: Mapped[str] = mapped_column(
        String(10), default=VerificationLevel.BASIC, server_default=VerificationLevel.BASIC.value
    )
    status: Mapped[str] = mapped_column(
        String(10), default=VisitStatus.VALID, server_default=VisitStatus.VALID.value
    )
    flags: Mapped[list[str]] = mapped_column(
        ARRAY(String(24)), default=list, server_default=text("'{}'")
    )
    # Plain words for the reviewer: "Same code also sent by 1 other number(s)."
    flag_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(String(200))


class PointsEntry(InsertOnlyEntity):
    """`points_ledger`. Never edited: a correction is a new `adjustment` row."""

    __tablename__ = "points_ledger"
    __table_args__ = (
        CheckConstraint(in_values("reason", PointsReason), name="reason_valid"),
        Index("ix_points_ledger_user_created_at", "user_id", "created_at"),
    )

    # Points go with the customer's data when it is deleted (the visits stay, anonymous).
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Null for adjustments not tied to a visit.
    visit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("visits.id", ondelete="RESTRICT"), index=True
    )
    reason: Mapped[str] = mapped_column(String(20))
    points: Mapped[int] = mapped_column(Integer)
