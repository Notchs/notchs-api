"""Venues: the cafes, restaurants and other places customers collect notches at."""

import uuid
from datetime import date, datetime
from decimal import Decimal
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
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity, in_values, public_code_column
from app.modules.locations.models import SLUG_REGEX
from app.modules.venues.scan_codes import MAX_LENGTH as SCAN_CODE_MAX_LENGTH
from app.modules.venues.scan_codes import SCAN_CODE_REGEX, random_scan_code

DEFAULT_GEOFENCE_RADIUS_M = 150
DEFAULT_FIRST_NOTCH_SPOTS = 50
MAX_FOUNDING_VENUE_NO = 30


class VenueCategory(StrEnum):
    CAFE = "cafe"
    RESTAURANT = "restaurant"
    BAR = "bar"
    HOTEL = "hotel"
    TURF_GYM = "turf_gym"
    OTHER = "other"


class VenueStatus(StrEnum):
    PENDING = "pending"
    SIGNED = "signed"
    COUNTER_LIVE = "counter_live"
    PAUSED = "paused"
    CLOSED = "closed"


class Venue(Entity):
    __tablename__ = "venues"
    __table_args__ = (
        UniqueConstraint("city_id", "slug", name="uq_venues_city_id_slug"),
        CheckConstraint(f"slug ~ '{SLUG_REGEX}'", name="slug_format"),
        CheckConstraint(f"scan_code ~ '{SCAN_CODE_REGEX}'", name="scan_code_format"),
        # Stored as text + CHECK, not a Postgres enum: adding a value is a plain migration.
        CheckConstraint(in_values("category", VenueCategory), name="category_valid"),
        CheckConstraint(in_values("status", VenueStatus), name="status_valid"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        CheckConstraint("(latitude IS NULL) = (longitude IS NULL)", name="coordinates_paired"),
        CheckConstraint("geofence_radius_m > 0", name="geofence_radius_positive"),
        CheckConstraint(
            f"founding_venue_no BETWEEN 1 AND {MAX_FOUNDING_VENUE_NO}",
            name="founding_venue_no_range",
        ),
        CheckConstraint("first_notch_spots >= 0", name="first_notch_spots_non_negative"),
        CheckConstraint("monthly_fee_paise >= 0", name="monthly_fee_paise_non_negative"),
    )

    # Public id shown to people (README, "IDs and public codes"). Not in the spec sheet.
    venue_code: Mapped[str] = public_code_column("VEN")
    # What customers send to get notched: "Hey Notchs!, get me notched. KAHWA-482913".
    # Made from the name when the venue is created (scan_codes.py); unique; admins may change it.
    # Not in the spec sheet (it had `code`, e.g. KAAFI-SBR; a hyphen now separates the live code).
    scan_code: Mapped[str] = mapped_column(
        String(SCAN_CODE_MAX_LENGTH), unique=True, default=random_scan_code
    )

    city_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cities.id", ondelete="RESTRICT"), index=True
    )
    area_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("areas.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    # Unique per city, used in the URL: /ahmedabad/v/kahwa-house
    slug: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(16), index=True)
    address: Mapped[str | None] = mapped_column(Text)
    # e.g. "Opposite Law Garden". Not in the sheet: comes from the application form.
    landmark: Mapped[str | None] = mapped_column(String(120))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    # Only used if geofencing is switched on later.
    geofence_radius_m: Mapped[int] = mapped_column(
        Integer,
        default=DEFAULT_GEOFENCE_RADIUS_M,
        server_default=str(DEFAULT_GEOFENCE_RADIUS_M),
    )
    # One line shown on the venue page.
    description: Mapped[str | None] = mapped_column(String(160))
    # S3 object key, never a full URL.
    photo_s3_key: Mapped[str | None] = mapped_column(String(255))
    # {"mon": [["08:00", "23:00"]], ...} in IST. Validated by `schemas.OpeningHours`.
    opening_hours: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(14),
        default=VenueStatus.PENDING,
        server_default=VenueStatus.PENDING.value,
        index=True,
    )
    # 1-30 only, a real "founding venue" number.
    founding_venue_no: Mapped[int | None] = mapped_column(Integer, unique=True)
    # How many customers can get the First Notch badge at this venue.
    first_notch_spots: Mapped[int] = mapped_column(
        Integer,
        default=DEFAULT_FIRST_NOTCH_SPOTS,
        server_default=str(DEFAULT_FIRST_NOTCH_SPOTS),
    )
    # Last day of the free months.
    free_until: Mapped[date | None] = mapped_column(Date)
    # Flat fee after the free months, in paise. The amount is still an open decision.
    monthly_fee_paise: Mapped[int | None] = mapped_column(BigInteger)
    # The admin who signed the venue.
    signed_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    # Soft delete. Deleted venues keep their slug, so old links never point at a new venue.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VenueMemberRole(StrEnum):
    OWNER = "owner"
    MANAGER = "manager"  # Phase 2. For now every venue has one login, its owner.


class VenueMember(Entity):
    """A person who logs in to the venue app. From the sheet, plus email + password login."""

    __tablename__ = "venue_members"
    __table_args__ = (
        UniqueConstraint("venue_id", "phone_e164", name="uq_venue_members_venue_id_phone_e164"),
        CheckConstraint(in_values("role", VenueMemberRole), name="role_valid"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    venue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("venues.id", ondelete="CASCADE"), index=True
    )
    phone_e164: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(60))
    role: Mapped[str] = mapped_column(
        String(12), default=VenueMemberRole.OWNER, server_default=VenueMemberRole.OWNER.value
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Login (not in the table sheet, which keeps the login design separate).
    email: Mapped[str] = mapped_column(String(120), unique=True)
    # argon2id. NULL until the member sets a password through the emailed link.
    password_hash: Mapped[str | None] = mapped_column(String(255))


DEFAULT_COUNTER_LABEL = "Counter 1"
DEFAULT_CODE_INTERVAL_S = 30


class CounterDevice(Entity):
    """A phone at the counter that shows the rotating QR code."""

    __tablename__ = "counter_devices"
    __table_args__ = (
        CheckConstraint("code_interval_s > 0", name="code_interval_positive"),
        # One counter phone per venue for now. Removed counters (revoked_at set) are kept for
        # history and do not count, so more counters later only means dropping this index.
        Index(
            "uq_counter_devices_active_venue",
            "venue_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    venue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("venues.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(
        String(40), default=DEFAULT_COUNTER_LABEL, server_default=DEFAULT_COUNTER_LABEL
    )
    # Encrypted TOTP secret for the time-based codes. Works offline on the phone.
    totp_secret_enc: Mapped[str] = mapped_column(String(255))
    code_interval_s: Mapped[int] = mapped_column(
        SmallInteger,
        default=DEFAULT_CODE_INTERVAL_S,
        server_default=str(DEFAULT_CODE_INTERVAL_S),
    )
    paired_by_member_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("venue_members.id", ondelete="SET NULL")
    )
    paired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Heartbeat: the owner's home screen shows when the phone is off.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # SHA-256 of the long-lived token the paired phone uses to prove itself. Not in the sheet.
    # NULL until the phone is paired.
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
