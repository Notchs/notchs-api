import uuid
from datetime import datetime

from pydantic import ConfigDict, Field

from app.common.schemas import BaseSchema, PublicCode
from app.modules.auth.emails import Email
from app.modules.auth.passwords import MAX_PASSWORD_LENGTH
from app.modules.venues.models import VenueStatus


class SessionOut(BaseSchema):
    """When the new access cookie expires, so the app knows when to refresh next.

    The tokens themselves are only in HttpOnly cookies, never in the response body.
    """

    access_expires_at: datetime


# Passwords are used exactly as typed: no trimming of spaces (BaseSchema trims by default).
_KEEP_SPACES = ConfigDict(str_strip_whitespace=False)


class LoginIn(BaseSchema):
    model_config = _KEEP_SPACES

    email: Email
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class SetPasswordIn(BaseSchema):
    """The token from the emailed link (first password or reset) and the new password."""

    model_config = _KEEP_SPACES

    token: str = Field(min_length=20, max_length=100)
    # Only the length cap here, so an over-long body is refused before hashing. The full
    # policy (passwords.check_password_policy) runs in the service with a clear message.
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)


class ChangePasswordIn(BaseSchema):
    model_config = _KEEP_SPACES

    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(max_length=MAX_PASSWORD_LENGTH)


class ChangeEmailIn(BaseSchema):
    model_config = _KEEP_SPACES  # the password; Email trims its own value

    new_email: Email
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class EmailCodeIn(BaseSchema):
    code: str = Field(pattern=r"^\s*[0-9]{6}\s*$")


class ForgotPasswordIn(BaseSchema):
    email: Email
    turnstile_token: str = Field(default="", max_length=2048)


class ConsumeLinkIn(BaseSchema):
    """The token from the WhatsApp login link (`/login/verify?token=...`)."""

    token: str = Field(min_length=20, max_length=100)


class CustomerBriefOut(BaseSchema):
    """Enough for the customer site to greet someone. The full profile comes with the customers
    module. The phone number is never returned."""

    id: uuid.UUID
    name: str | None
    handle: str | None
    is_public: bool
    session: SessionOut | None = None


class AdminMeOut(BaseSchema):
    id: uuid.UUID
    admin_user_code: PublicCode
    name: str
    email: str
    role: str
    session: SessionOut | None = None


class VenueSummaryOut(BaseSchema):
    id: uuid.UUID
    venue_code: PublicCode
    name: str
    slug: str
    status: VenueStatus


class VenueMeOut(BaseSchema):
    id: uuid.UUID
    name: str
    email: str
    role: str
    venue: VenueSummaryOut
    session: SessionOut | None = None
