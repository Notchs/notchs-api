"""Venues: the cafes, restaurants and other places customers collect notches at."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity, public_code_column
from app.modules.locations.models import SLUG_REGEX

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


def _in_values(column: str, enum: type[StrEnum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({values})"


class Venue(Entity):
    __tablename__ = "venues"
    __table_args__ = (
        UniqueConstraint("city_id", "slug", name="uq_venues_city_id_slug"),
        CheckConstraint(f"slug ~ '{SLUG_REGEX}'", name="slug_format"),
        # Stored as text + CHECK, not a Postgres enum: adding a value is a plain migration.
        CheckConstraint(_in_values("category", VenueCategory), name="category_valid"),
        CheckConstraint(_in_values("status", VenueStatus), name="status_valid"),
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

    # Public id shown to people (docs/WORKPLAN.md §1.1). Not in the spec sheet.
    venue_code: Mapped[str] = public_code_column("VEN")

    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cities.id"), index=True)
    area_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("areas.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    # Unique per city, used in the URL: /ahmedabad/v/kahwa-house
    slug: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(16), index=True)
    address: Mapped[str | None] = mapped_column(Text)
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
    signed_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id"))
    # Soft delete. Deleted venues keep their slug, so old links never point at a new venue.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
