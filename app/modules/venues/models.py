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
from app.modules.auth.enums import AccountStatus
from app.modules.locations.models import SLUG_REGEX
from app.modules.users.models import INSTAGRAM_REGEX
from app.modules.venues.scan_codes import MAX_LENGTH as SCAN_CODE_MAX_LENGTH
from app.modules.venues.scan_codes import SCAN_CODE_REGEX, random_scan_code

DEFAULT_GEOFENCE_RADIUS_M = 150
DEFAULT_FIRST_NOTCH_SPOTS = 50
PINCODE_REGEX = "^[0-9]{6}$"
MAX_PHOTOS_PER_VENUE = 10


class VenueCategory(StrEnum):
    CAFE = "cafe"
    RESTAURANT = "restaurant"
    BAR = "bar"
    HOTEL = "hotel"
    TURF_GYM = "turf_gym"  # turfs, box grounds and gyms
    THEATRE = "theatre"
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
        CheckConstraint("founding_venue_no >= 1", name="founding_venue_no_positive"),
        CheckConstraint(f"instagram_handle ~ '{INSTAGRAM_REGEX}'", name="instagram_handle_format"),
        # Numbered within each kind of place (venues/founding.py).
        UniqueConstraint(
            "category", "founding_venue_no", name="uq_venues_category_founding_venue_no"
        ),
        CheckConstraint("first_notch_spots >= 0", name="first_notch_spots_non_negative"),
        CheckConstraint("monthly_fee_paise >= 0", name="monthly_fee_paise_non_negative"),
        CheckConstraint(f"pincode ~ '{PINCODE_REGEX}'", name="pincode_format"),
        CheckConstraint("price_range BETWEEN 1 AND 4", name="price_range_valid"),
        Index(
            "uq_venues_google_place_id",
            "google_place_id",
            unique=True,
            postgresql_where=text("google_place_id IS NOT NULL"),
        ),
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
    # The longer "about" text on the venue page (`description` is its one-line tagline).
    about: Mapped[str | None] = mapped_column(Text)
    # Links and contact the owner shows on their venue page (venues/profile.py).
    menu_url: Mapped[str | None] = mapped_column(String(300))
    website_url: Mapped[str | None] = mapped_column(String(300))
    instagram_handle: Mapped[str | None] = mapped_column(String(30))
    # The place's own public number, as the owner writes it (never a customer's).
    contact_phone: Mapped[str | None] = mapped_column(String(20))
    # From AMENITIES in venues/profile.py, e.g. ["Wi-Fi", "Parking"].
    amenities: Mapped[list[str] | None] = mapped_column(JSONB)
    # Whether the place may appear on Discover once it opens.
    visible_on_discover: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
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
    # Its place among the first places of its kind to join (venues/founding.py): #1 to #100
    # (#200 for turfs), 25% off for life. None after the founding spots are gone.
    founding_venue_no: Mapped[int | None] = mapped_column(Integer)
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
    # When the status moved on: signed, counter first live (real "New on Notchs"), closed.
    # Set by the services; old venues only have what is known from real rows.
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    live_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Why it is paused or closed, written by the team or the owner. Never shown publicly.
    status_reason: Mapped[str | None] = mapped_column(String(200))
    pincode: Mapped[str | None] = mapped_column(String(6))
    # Google Maps place id, typed in by the team or the venue (no Google API calls): the
    # "Directions" link and duplicate checks. Unique when set.
    google_place_id: Mapped[str | None] = mapped_column(String(255))
    # 1 to 4 (₹ to ₹₹₹₹).
    price_range: Mapped[int | None] = mapped_column(SmallInteger)
    # S3 object key, never a full URL. `photo_s3_key` stays the cover photo.
    logo_s3_key: Mapped[str | None] = mapped_column(String(255))


class VenuePhoto(Entity):
    """More photos for the venue page (a gallery). At most MAX_PHOTOS_PER_VENUE, enforced in the
    service. The cover stays `venues.photo_s3_key`."""

    __tablename__ = "venue_photos"
    __table_args__ = (Index("ix_venue_photos_venue_id_sort_order", "venue_id", "sort_order"),)

    venue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("venues.id", ondelete="CASCADE"))
    # S3 object key, never a full URL.
    s3_key: Mapped[str] = mapped_column(String(255))
    caption: Mapped[str | None] = mapped_column(String(120))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


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
        CheckConstraint(in_values("status", AccountStatus), name="status_valid"),
        # Rows disabled before 0012 have no date; a date is only ever set on a disabled row.
        CheckConstraint(
            "status = 'disabled' OR disabled_at IS NULL", name="disabled_at_only_when_disabled"
        ),
        CheckConstraint(
            "status <> 'active' OR password_hash IS NOT NULL", name="active_has_password"
        ),
        CheckConstraint(
            "(mfa_enabled_at IS NULL) OR (mfa_secret_enc IS NOT NULL)", name="mfa_has_secret"
        ),
    )

    venue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("venues.id", ondelete="CASCADE"), index=True
    )
    phone_e164: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(60))
    role: Mapped[str] = mapped_column(
        String(12), default=VenueMemberRole.OWNER, server_default=VenueMemberRole.OWNER.value
    )
    # invited until the emailed set-password link is used.
    status: Mapped[str] = mapped_column(
        String(10), default=AccountStatus.INVITED, server_default=AccountStatus.INVITED.value
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Login (not in the table sheet, which keeps the login design separate). Unique across all
    # venues: one person with two venues needs two emails until chains are supported.
    email: Mapped[str] = mapped_column(String(120), unique=True)
    # argon2id. NULL until the member sets a password through the emailed link.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The owner who added a manager (managers come later).
    invited_by_member_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("venue_members.id", ondelete="SET NULL")
    )
    # Two-factor login (TOTP), optional for owners. Fernet-encrypted, never stored plain. The
    # login step and setup screen come later.
    mfa_secret_enc: Mapped[str | None] = mapped_column(String(255))
    mfa_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


DEFAULT_COUNTER_LABEL = "Counter 1"
DEFAULT_CODE_INTERVAL_S = 30


class CounterStatus(StrEnum):
    PENDING_PAIRING = "pending_pairing"  # made, no phone has paired yet
    ACTIVE = "active"  # a paired phone shows the codes
    REVOKED = "revoked"  # removed; kept for history


class CounterRevokeReason(StrEnum):
    REMOVED_BY_VENUE = "removed_by_venue"  # the owner removed it without saying why
    LOST = "lost"
    REPLACED = "replaced"  # a new phone was paired instead
    VENUE_CLOSED = "venue_closed"
    ADMIN = "admin"


class CounterDevice(Entity):
    """A phone at the counter that shows the rotating QR code."""

    __tablename__ = "counter_devices"
    __table_args__ = (
        CheckConstraint("code_interval_s > 0", name="code_interval_positive"),
        CheckConstraint(in_values("status", CounterStatus), name="status_valid"),
        CheckConstraint(
            in_values("revoke_reason", CounterRevokeReason), name="revoke_reason_valid"
        ),
        CheckConstraint(
            "(status = 'revoked') = (revoked_at IS NOT NULL)", name="revoked_at_when_revoked"
        ),
        # Counters removed before 0012 have no reason.
        CheckConstraint(
            "revoke_reason IS NULL OR revoked_at IS NOT NULL", name="revoke_reason_when_revoked"
        ),
        CheckConstraint("status <> 'active' OR token_hash IS NOT NULL", name="active_is_paired"),
        # One counter phone per venue for now. Removed counters are kept for history and do
        # not count, so more counters later only means dropping this index.
        Index(
            "uq_counter_devices_active_venue",
            "venue_id",
            unique=True,
            postgresql_where=text("status <> 'revoked'"),
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
    status: Mapped[str] = mapped_column(
        String(16),
        default=CounterStatus.PENDING_PAIRING,
        server_default=CounterStatus.PENDING_PAIRING.value,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(16))
    # The venue login that removed it (removals by the team go to audit_logs).
    revoked_by_member_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("venue_members.id", ondelete="SET NULL")
    )
    # Sent by the phone: its app version (each heartbeat) and model and OS (when pairing).
    app_version: Mapped[str | None] = mapped_column(String(20))
    device_model: Mapped[str | None] = mapped_column(String(80))
    # For alerts to the phone later; the push service is still to be chosen. A secret: never
    # logged or returned.
    push_token: Mapped[str | None] = mapped_column(String(255))
    # SHA-256 of the long-lived token the paired phone uses to prove itself. Not in the sheet.
    # NULL until the phone is paired.
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
