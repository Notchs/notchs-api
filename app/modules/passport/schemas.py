"""Request and response bodies for the customer's passport. The phone number appears only in the
customer's own `GET /me` (team decision, 18 Nov 2026), never in anything another person sees."""

import re
import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator

from app.common.schemas import BaseSchema
from app.modules.users.handles import MAX_NAME_LENGTH, clean_name
from app.modules.users.models import INSTAGRAM_REGEX, UserTheme


class PlaceRef(BaseSchema):
    id: uuid.UUID
    name: str


class Totals(BaseSchema):
    notches: int
    places: int
    points: int


class AgeOut(BaseSchema):
    # Empty until the customer adds it in their profile.
    birth_date: date | None
    # Worked out from birth_date: "18-24", "25-34", "35-44" or "45+".
    age_range: str | None
    # When they confirmed they are 18 or over (on WhatsApp, with the privacy notice).
    adult_confirmed_at: datetime | None
    # The exact words to show next to the date-of-birth field; stored when they save it.
    consent_text: str
    consent_version: str


class MeOut(BaseSchema):
    name: str | None
    handle: str
    # Their own number, in full: only ever in their own profile.
    phone: str | None
    # A signed link that works for 15 minutes; None without a photo (or before storage is set up).
    photo_url: str | None
    city: PlaceRef | None
    area: PlaceRef | None
    is_public: bool
    theme: UserTheme
    founding_member_no: int | None
    instagram_handle: str | None
    show_instagram_to_venues: bool
    member_since: datetime
    age: AgeOut
    totals: Totals


class MeUpdate(BaseSchema):
    """Only the fields sent are changed. `instagram_handle: null` removes it."""

    name: Annotated[str, Field(min_length=1, max_length=MAX_NAME_LENGTH)] | None = None
    area_id: uuid.UUID | None = None
    theme: UserTheme | None = None
    instagram_handle: Annotated[str, Field(max_length=31)] | None = None
    show_instagram_to_venues: bool | None = None
    # `null` removes it. Must make them 18 or over.
    birth_date: date | None = None

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = clean_name(value)
        if cleaned is None:
            raise ValueError("Enter your name.")
        return cleaned

    @field_validator("instagram_handle")
    @classmethod
    def _instagram(cls, value: str | None) -> str | None:
        if value is None:
            return None
        handle = value.removeprefix("@").lower()
        if not re.fullmatch(INSTAGRAM_REGEX, handle):
            raise ValueError("Enter an Instagram username: letters, numbers, dots and underscores.")
        return handle


class NotchOut(BaseSchema):
    notch_no: int
    venue_name: str
    venue_area: str | None
    niche: str
    colour: str
    visit_date: date
    visited_at: datetime
    # This customer's 1st, 2nd... visit at the venue.
    visit_no_at_venue: int
    points: int


class VisitedPlaceOut(BaseSchema):
    venue_code: str
    venue_name: str
    area: str | None
    city: str
    visits: int
    first_visit: date
    last_visit: date


class CategoryPlacesOut(BaseSchema):
    """One kind of place (niche): cafés, restaurants, turfs..."""

    niche: str
    label: str
    colour: str
    places: int
    visits: int
    venues: list[VisitedPlaceOut]


class AreaNotchsOut(BaseSchema):
    # None for places with no area set.
    area: PlaceRef | None
    notchs: int
    places: int


class CityNotchsOut(BaseSchema):
    city: PlaceRef
    notchs: int
    places: int
    areas: list[AreaNotchsOut]


class RewardOut(BaseSchema):
    month: str
    new_cafes: int
    needed: int
    eligible: bool
    value_rupees: int


class PhoneChangeOut(BaseSchema):
    """Send `message` to Notchs on WhatsApp from the new number (the link pre-types it)."""

    message: str
    expires_at: datetime
    # None until the Notchs WhatsApp number is set up.
    whatsapp_link: str | None


class DeleteMeIn(BaseSchema):
    # Typed by the customer, so a stray click can't delete an account.
    confirm: Literal["DELETE"]
