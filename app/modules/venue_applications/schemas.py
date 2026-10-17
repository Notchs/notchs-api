"""Request and response bodies for the "apply as a venue" form."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import AfterValidator, Field, model_validator

from app.common.phone import Phone
from app.common.schemas import BaseSchema, EntitySchema, PublicCode
from app.modules.auth.emails import Email
from app.modules.venue_applications.models import ApplicationStatus
from app.modules.venues.models import VenueCategory
from app.modules.venues.schemas import Latitude, Longitude

MAX_MAPS_URL_LENGTH = 500
MAX_NOTES_LENGTH = 1000

# Exact hosts only, so a look-alike domain can never pass.
_MAPS_SHORT_HOSTS = {"maps.app.goo.gl"}  # what Google Maps' Share button produces
_GOO_GL_HOSTS = {"goo.gl", "www.goo.gl"}  # older short links, /maps/... only
_MAPS_HOSTS = {"maps.google.com", "maps.google.co.in"}
_GOOGLE_HOSTS = {"google.com", "www.google.com", "google.co.in", "www.google.co.in"}


def _is_google_host(host: str) -> bool:
    return host in _GOOGLE_HOSTS


def check_google_maps_url(value: str) -> str:
    url = value.strip()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        raise ValueError("Paste the https:// link from Google Maps' Share button")
    is_maps = (
        host in _MAPS_SHORT_HOSTS
        or host in _MAPS_HOSTS
        or (host in _GOO_GL_HOSTS and parts.path.startswith("/maps"))
        or (_is_google_host(host) and parts.path.startswith("/maps"))
    )
    if not is_maps:
        raise ValueError("This is not a Google Maps link")
    return url


GoogleMapsUrl = Annotated[
    str, Field(max_length=MAX_MAPS_URL_LENGTH), AfterValidator(check_google_maps_url)
]


class ApplicationCreate(BaseSchema):
    """The application form. The email is not here: it comes from the verification token."""

    verification_token: str = Field(min_length=20, max_length=100)
    venue_name: str = Field(min_length=1, max_length=120)
    category: VenueCategory
    city_id: uuid.UUID
    google_maps_url: GoogleMapsUrl
    contact_name: str = Field(min_length=1, max_length=60)
    phone: Phone
    landmark: str | None = Field(None, max_length=120)
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    notes: str | None = Field(None, max_length=MAX_NOTES_LENGTH)

    @model_validator(mode="after")
    def _coordinates_come_in_pairs(self) -> Self:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be given together")
        return self

    @model_validator(mode="after")
    def _blank_optional_text_is_none(self) -> Self:
        if self.landmark == "":
            self.landmark = None
        if self.notes == "":
            self.notes = None
        return self


class ApplicationCreated(BaseSchema):
    application_code: PublicCode


class ApplicationOut(EntitySchema):
    """One application, for the admin panel."""

    application_code: PublicCode
    status: ApplicationStatus
    venue_name: str
    category: VenueCategory
    city_id: uuid.UUID
    google_maps_url: str
    latitude: Decimal | None
    longitude: Decimal | None
    landmark: str | None
    contact_name: str
    email: str
    phone_e164: str
    notes: str | None
    email_verified_at: datetime
    reviewed_by_admin_id: uuid.UUID | None
    reviewed_at: datetime | None
    rejection_reason: str | None
    venue_id: uuid.UUID | None


class ApproveIn(BaseSchema):
    """What the admin fills in on approval. The location defaults to the applicant's, if sent."""

    area_id: uuid.UUID | None = None
    address: str | None = Field(None, max_length=500)
    latitude: Latitude | None = None
    longitude: Longitude | None = None

    @model_validator(mode="after")
    def _coordinates_come_in_pairs(self) -> Self:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be given together")
        return self


class ApprovedOut(BaseSchema):
    application: ApplicationOut
    venue_id: uuid.UUID
    venue_code: PublicCode
    venue_slug: str
    owner_email: str


class RejectIn(BaseSchema):
    reason: str | None = Field(None, max_length=255)
    # Email the applicant (with the reason, if given).
    notify: bool = True


class SendCodeIn(BaseSchema):
    email: Email
    turnstile_token: str = Field(default="", max_length=2048)


class VerifyCodeIn(BaseSchema):
    email: Email
    code: str = Field(pattern=r"^\s*[0-9]{6}\s*$")


class EmailVerifiedOut(BaseSchema):
    """Send this token with the form. It proves the email was verified and expires."""

    verification_token: str
    expires_in_seconds: int
