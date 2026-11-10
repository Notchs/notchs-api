"""Request and response bodies for the customer's passport. Never a phone number."""

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


class MeOut(BaseSchema):
    name: str | None
    handle: str
    city: PlaceRef | None
    area: PlaceRef | None
    is_public: bool
    theme: UserTheme
    founding_member_no: int | None
    instagram_handle: str | None
    show_instagram_to_venues: bool
    member_since: datetime
    totals: Totals


class MeUpdate(BaseSchema):
    """Only the fields sent are changed. `instagram_handle: null` removes it."""

    name: Annotated[str, Field(min_length=1, max_length=MAX_NAME_LENGTH)] | None = None
    area_id: uuid.UUID | None = None
    theme: UserTheme | None = None
    instagram_handle: Annotated[str, Field(max_length=31)] | None = None
    show_instagram_to_venues: bool | None = None

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
