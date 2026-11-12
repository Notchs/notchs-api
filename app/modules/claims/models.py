"""Bill-photo claims: a Notch the counter phone could not give (team decision: the customer files
a support claim with a bill photo).

    customer   picks the place on the website -> CLM- code, WhatsApp opens pre-typed
               "CLAIM CLM-7K3M9QX2", then sends the bill photo (the same day, in India)
    admin      looks at the photo (fetched from WhatsApp when asked), approves or rejects
    approved   a normal visit with points, marked `verified` (a bill was checked)

The photo itself is never stored by Notchs: only WhatsApp's media id. WhatsApp keeps media for
about 30 days, so a claim must be checked within that time.
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
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity, in_values, public_code_column


class ClaimStatus(StrEnum):
    AWAITING_PHOTO = "awaiting_photo"  # started on the website; no photo yet
    IN_REVIEW = "in_review"  # the photo arrived: an admin checks it
    APPROVED = "approved"  # became a visit
    REJECTED = "rejected"  # see reject_code / reject_reason
    # Closed on its own: no photo by the end of its day, or not reviewed while WhatsApp still
    # had the photo (about 30 days).
    EXPIRED = "expired"


class ClaimRejectCode(StrEnum):
    UNREADABLE = "unreadable"
    WRONG_VENUE = "wrong_venue"
    WRONG_DATE = "wrong_date"
    DUPLICATE_BILL = "duplicate_bill"
    OTHER = "other"


class VisitClaim(Entity):
    __tablename__ = "visit_claims"
    __table_args__ = (
        CheckConstraint(in_values("status", ClaimStatus), name="status_valid"),
        CheckConstraint(in_values("reject_code", ClaimRejectCode), name="reject_code_valid"),
        # One live claim per customer, place and day; a rejected or expired one can be tried
        # again.
        Index(
            "uq_visit_claims_user_venue_day",
            "user_id",
            "venue_id",
            "visit_date",
            unique=True,
            postgresql_where=text("status NOT IN ('rejected', 'expired')"),
        ),
        # The same bill can be approved once per venue.
        Index(
            "uq_visit_claims_venue_bill_number",
            "venue_id",
            "bill_number",
            unique=True,
            postgresql_where=text("bill_number IS NOT NULL AND status = 'approved'"),
        ),
        Index("ix_visit_claims_photo_sha256", "photo_sha256"),
        CheckConstraint("bill_amount_paise >= 0", name="bill_amount_non_negative"),
        CheckConstraint("photo_sha256 ~ '^[0-9a-f]{64}$'", name="photo_sha256_format"),
        CheckConstraint(
            "(status = 'expired') = (expired_at IS NOT NULL)", name="expired_at_when_expired"
        ),
        # The review queue, oldest first.
        Index("ix_visit_claims_status_created_at", "status", "created_at"),
        Index("ix_visit_claims_user_created_at", "user_id", "created_at"),
        CheckConstraint(
            "(status = 'awaiting_photo') = (photo_received_at IS NULL)",
            name="photo_after_start",
        ),
        CheckConstraint(
            "(status = 'approved') = (visit_id IS NOT NULL)", name="visit_when_approved"
        ),
        CheckConstraint(
            "(status = 'rejected') = (reject_code IS NOT NULL)", name="code_when_rejected"
        ),
    )

    claim_code: Mapped[str] = public_code_column("CLM")
    # Deleting a customer deletes their claims.
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    venue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("venues.id", ondelete="RESTRICT"))
    # The day in India the claim is for: the day it was started.
    visit_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(16), default=ClaimStatus.AWAITING_PHOTO, server_default=ClaimStatus.AWAITING_PHOTO
    )
    # The WhatsApp message with the bill photo, and WhatsApp's id for the image.
    photo_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("whatsapp_messages.id", ondelete="SET NULL"), unique=True
    )
    photo_media_id: Mapped[str | None] = mapped_column(String(128))
    photo_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Why it was rejected, as a code (for counts and standard wording) ...
    reject_code: Mapped[str | None] = mapped_column(String(16))
    # ... and optional extra words. Both are shown to the customer.
    reject_reason: Mapped[str | None] = mapped_column(String(200))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Typed by the admin from the bill while reviewing: catches the same bill claimed twice.
    bill_number: Mapped[str | None] = mapped_column(String(40))
    bill_amount_paise: Mapped[int | None] = mapped_column(BigInteger)
    bill_date: Mapped[date | None] = mapped_column(Date)
    # SHA-256 of the photo, worked out when an admin opens it (the photo is never stored). A
    # match with another claim is a warning, not an automatic rejection.
    photo_sha256: Mapped[str | None] = mapped_column(String(64))
    # From the customer when starting the claim, e.g. "the counter phone was off".
    customer_note: Mapped[str | None] = mapped_column(String(300))
    # For the team only, never returned to the customer.
    admin_note: Mapped[str | None] = mapped_column(Text)
    visit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("visits.id", ondelete="RESTRICT"), unique=True
    )
