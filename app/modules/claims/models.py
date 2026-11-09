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

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity, in_values, public_code_column


class ClaimStatus(StrEnum):
    AWAITING_PHOTO = "awaiting_photo"  # started on the website; no photo yet
    IN_REVIEW = "in_review"  # the photo arrived: an admin checks it
    APPROVED = "approved"  # became a visit
    REJECTED = "rejected"  # see reject_reason


class VisitClaim(Entity):
    __tablename__ = "visit_claims"
    __table_args__ = (
        CheckConstraint(in_values("status", ClaimStatus), name="status_valid"),
        # One live claim per customer, place and day; a rejected one can be tried again.
        Index(
            "uq_visit_claims_user_venue_day",
            "user_id",
            "venue_id",
            "visit_date",
            unique=True,
            postgresql_where=text("status <> 'rejected'"),
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
            "(status = 'rejected') = (reject_reason IS NOT NULL)", name="reason_when_rejected"
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
    # Shown to the customer.
    reject_reason: Mapped[str | None] = mapped_column(String(200))
    visit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("visits.id", ondelete="RESTRICT"), unique=True
    )
