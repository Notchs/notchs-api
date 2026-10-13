import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, StringConstraints, model_validator

from app.common.schemas import BaseSchema, EntitySchema, PublicCode
from app.modules.locations.models import SLUG_REGEX
from app.modules.venues.models import (
    DEFAULT_FIRST_NOTCH_SPOTS,
    DEFAULT_GEOFENCE_RADIUS_M,
    MAX_FOUNDING_VENUE_NO,
    VenueCategory,
    VenueStatus,
)

Day = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ClockTime = Annotated[str, StringConstraints(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]
TimeRange = tuple[ClockTime, ClockTime]
MAX_RANGES_PER_DAY = 4
# Columns that are NOT NULL: a partial update may leave them out but not send null.
NOT_NULL_ON_UPDATE = ("name", "slug", "category", "geofence_radius_m", "first_notch_spots")


def _check_opening_hours(value: dict[Day, list[TimeRange]]) -> dict[Day, list[TimeRange]]:
    for day, ranges in value.items():
        if len(ranges) > MAX_RANGES_PER_DAY:
            raise ValueError(f"{day}: at most {MAX_RANGES_PER_DAY} time ranges per day")
        for opens, closes in ranges:
            # closes < opens is allowed: it means the venue closes after midnight.
            if opens == closes:
                raise ValueError(f"{day}: opening and closing time are the same ({opens})")
    return value


# {"mon": [["08:00", "23:00"]], "tue": []} in IST. A missing or empty day means closed.
OpeningHours = Annotated[dict[Day, list[TimeRange]], AfterValidator(_check_opening_hours)]

Slug = Annotated[str, StringConstraints(pattern=SLUG_REGEX, max_length=120)]
Latitude = Annotated[Decimal, Field(ge=-90, le=90, max_digits=9, decimal_places=6)]
Longitude = Annotated[Decimal, Field(ge=-180, le=180, max_digits=9, decimal_places=6)]


class VenueCreate(BaseSchema):
    city_id: uuid.UUID
    area_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=120)
    # Generated from the name when left out.
    slug: Slug | None = None
    category: VenueCategory
    address: str | None = None
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    geofence_radius_m: int = Field(DEFAULT_GEOFENCE_RADIUS_M, gt=0)
    description: str | None = Field(None, max_length=160)
    photo_s3_key: str | None = Field(None, max_length=255)
    opening_hours: OpeningHours | None = None
    founding_venue_no: int | None = Field(None, ge=1, le=MAX_FOUNDING_VENUE_NO)
    first_notch_spots: int = Field(DEFAULT_FIRST_NOTCH_SPOTS, ge=0)
    free_until: date | None = None
    monthly_fee_paise: int | None = Field(None, ge=0)

    @model_validator(mode="after")
    def _coordinates_come_in_pairs(self) -> Self:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be given together")
        return self


class VenueUpdate(BaseSchema):
    """Partial update: only the fields that are sent change. The city cannot be changed."""

    area_id: uuid.UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=120)
    slug: Slug | None = None
    category: VenueCategory | None = None
    address: str | None = None
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    geofence_radius_m: int | None = Field(None, gt=0)
    description: str | None = Field(None, max_length=160)
    photo_s3_key: str | None = Field(None, max_length=255)
    opening_hours: OpeningHours | None = None
    founding_venue_no: int | None = Field(None, ge=1, le=MAX_FOUNDING_VENUE_NO)
    first_notch_spots: int | None = Field(None, ge=0)
    free_until: date | None = None
    monthly_fee_paise: int | None = Field(None, ge=0)

    @model_validator(mode="after")
    def _check_nulls_and_coordinates(self) -> Self:
        sent = self.model_fields_set
        for field in NOT_NULL_ON_UPDATE:
            if field in sent and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if {"latitude", "longitude"} & sent and (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be changed together")
        return self


class VenueStatusUpdate(BaseSchema):
    status: VenueStatus


class VenueOut(EntitySchema):
    """Full venue, for admins and the venue's own dashboard."""

    venue_code: PublicCode
    city_id: uuid.UUID
    area_id: uuid.UUID | None
    name: str
    slug: str
    category: VenueCategory
    address: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    geofence_radius_m: int
    description: str | None
    photo_s3_key: str | None
    opening_hours: OpeningHours | None
    status: VenueStatus
    founding_venue_no: int | None
    first_notch_spots: int
    free_until: date | None
    monthly_fee_paise: int | None
    signed_by_admin_id: uuid.UUID | None
    deleted_at: datetime | None = None


class VenuePublicOut(BaseSchema):
    """What anyone may see on the venue page. No billing or internal fields."""

    venue_code: PublicCode
    name: str
    slug: str
    category: VenueCategory
    address: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    description: str | None
    photo_s3_key: str | None
    opening_hours: OpeningHours | None
    founding_venue_no: int | None
