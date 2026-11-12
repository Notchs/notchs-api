"""Cities and the areas inside them. Lookup tables: seeded or imported, not created by users."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity, in_values

# Lowercase words joined by single hyphens, e.g. `prahlad-nagar`. Shared by every slug column.
SLUG_REGEX = "^[a-z0-9]+(-[a-z0-9]+)*$"
DEFAULT_TIMEZONE = "Asia/Kolkata"
DEFAULT_COUNTRY = "IN"
# A comma-joined list of Indian PIN codes (or nothing): checks every item of a text[] at once.
PINCODES_REGEX = "^([0-9]{6}(,[0-9]{6})*)?$"


class CityStatus(StrEnum):
    LIVE = "live"  # open to customers and venues
    COMING_SOON = "coming_soon"  # shown as "coming soon"; venues may apply
    HIDDEN = "hidden"  # not shown anywhere


class AreaStatus(StrEnum):
    LIVE = "live"
    HIDDEN = "hidden"  # kept for old venues and customers, not offered in dropdowns


class AreaSource(StrEnum):
    """Where an area came from. Re-imports match on (source, external_id), so never duplicate."""

    OSM = "osm"  # OpenStreetMap (team decision 12 Nov 2026)
    INDIA_POST = "india_post"
    GOOGLE = "google"  # only after the team's cost and terms check
    MANUAL = "manual"  # added by an admin, e.g. from a venue's "my area isn't listed"


def coordinate_checks() -> tuple[CheckConstraint, ...]:
    """Latitude/longitude ranges, set together or not at all. Shared by every table with both."""
    return (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        CheckConstraint("(latitude IS NULL) = (longitude IS NULL)", name="coordinates_paired"),
    )


class City(Entity):
    __tablename__ = "cities"
    __table_args__ = (
        CheckConstraint(f"slug ~ '{SLUG_REGEX}'", name="slug_format"),
        CheckConstraint(in_values("status", CityStatus), name="status_valid"),
        CheckConstraint("country ~ '^[A-Z]{2}$'", name="country_format"),
        *coordinate_checks(),
    )

    name: Mapped[str] = mapped_column(String(80))
    # Used in public URLs: /ahmedabad/v/kahwa-house
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    state: Mapped[str] = mapped_column(String(80))
    # Where "today" starts and ends for this city (IST day boundaries).
    timezone: Mapped[str] = mapped_column(
        String(40), default=DEFAULT_TIMEZONE, server_default=DEFAULT_TIMEZONE
    )
    status: Mapped[str] = mapped_column(
        String(12), default=CityStatus.HIDDEN, server_default=CityStatus.HIDDEN.value, index=True
    )
    # Map centre of the city.
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    # When the city went live (set by the admin tools; never made up for old cities).
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Lower first, then A to Z.
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # ISO 3166-1 alpha-2.
    country: Mapped[str] = mapped_column(
        String(2), default=DEFAULT_COUNTRY, server_default=DEFAULT_COUNTRY
    )
    # S3 object key, never a full URL.
    image_s3_key: Mapped[str | None] = mapped_column(String(255))


class Area(Entity):
    __tablename__ = "areas"
    __table_args__ = (
        UniqueConstraint("city_id", "slug", name="uq_areas_city_id_slug"),
        UniqueConstraint("source", "external_id", name="uq_areas_source_external_id"),
        CheckConstraint(f"slug ~ '{SLUG_REGEX}'", name="slug_format"),
        CheckConstraint(in_values("status", AreaStatus), name="status_valid"),
        CheckConstraint(in_values("source", AreaSource), name="source_valid"),
        CheckConstraint(
            f"array_to_string(pincodes, ',') ~ '{PINCODES_REGEX}'", name="pincodes_format"
        ),
        *coordinate_checks(),
    )

    city_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cities.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(80))
    slug: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(
        String(8), default=AreaStatus.LIVE, server_default=AreaStatus.LIVE.value
    )
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    source: Mapped[str] = mapped_column(
        String(12), default=AreaSource.MANUAL, server_default=AreaSource.MANUAL.value
    )
    # The source's own id, e.g. an OpenStreetMap node/way id. Unique per source.
    external_id: Mapped[str | None] = mapped_column(Text)
    # Other spellings people type: "CG Road", "C.G. Road".
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'::text[]")
    )
    # Six-digit PIN codes the area covers.
    pincodes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'::text[]")
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
