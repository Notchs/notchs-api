"""Applications from the public "apply as a venue" form. Not in the sheet (docs/modules/auth.md)."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity, in_values, public_code_column
from app.modules.venues.models import VenueCategory


class ApplicationStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class VenueApplication(Entity):
    __tablename__ = "venue_applications"
    __table_args__ = (
        CheckConstraint(in_values("category", VenueCategory), name="category_valid"),
        CheckConstraint(in_values("status", ApplicationStatus), name="status_valid"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        CheckConstraint("(latitude IS NULL) = (longitude IS NULL)", name="coordinates_paired"),
        # One open application per email. Rejected applicants may apply again.
        Index(
            "uq_venue_applications_pending_email",
            "email",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        # The admin queue: pending first, oldest first.
        Index("ix_venue_applications_status_created_at", "status", "created_at"),
    )

    application_code: Mapped[str] = public_code_column("APP")
    venue_name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(16))
    # Share link from Google Maps, pasted by the applicant (google.com/maps, maps.app.goo.gl, ...).
    # The admin checks the pin and sets the venue's latitude/longitude on approval.
    google_maps_url: Mapped[str] = mapped_column(String(500))
    # Optional: from the form's "use my current location" button.
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    # e.g. "Opposite Law Garden". No typed address: the Maps link pins the place, and the
    # admin sets the venue's area and address on approval.
    landmark: Mapped[str | None] = mapped_column(String(120))
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cities.id", ondelete="RESTRICT"))
    contact_name: Mapped[str] = mapped_column(String(60))
    # Becomes the venue member's login email on approval.
    email: Mapped[str] = mapped_column(String(120))
    # When the emailed 6-digit code was accepted. An application cannot exist without it.
    email_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    phone_e164: Mapped[str] = mapped_column(String(16))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(10),
        default=ApplicationStatus.PENDING,
        server_default=ApplicationStatus.PENDING.value,
    )
    reviewed_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(String(255))
    # The venue created on approval.
    venue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("venues.id", ondelete="SET NULL"))
