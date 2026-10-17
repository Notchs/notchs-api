"""Session cookies (README, "Sessions (for the frontends)").

Each audience has its own pair, so the venue app, the admin panel and the customer site never
overwrite each other (locally they all run on localhost, and cookies ignore the port):

    nx_<audience>_at   access JWT, Path=/, lives as long as the JWT
    nx_<audience>_rt   refresh token, Path=/api/v1/auth/<audience>, so the browser only sends
                       it to that audience's refresh and logout endpoints
"""

from datetime import UTC, datetime
from enum import StrEnum

from starlette.responses import Response

from app.core.config import settings
from app.modules.auth.enums import SubjectType


class Audience(StrEnum):
    """The `{audience}` part of `/auth/{audience}/refresh`, one per app."""

    USER = "user"
    VENUE = "venue"
    ADMIN = "admin"


AUDIENCE_SUBJECT = {
    Audience.USER: SubjectType.USER,
    Audience.VENUE: SubjectType.VENUE_MEMBER,
    Audience.ADMIN: SubjectType.ADMIN,
}
SUBJECT_AUDIENCE = {subject: audience for audience, subject in AUDIENCE_SUBJECT.items()}


def access_cookie_name(subject_type: SubjectType) -> str:
    return f"nx_{SUBJECT_AUDIENCE[subject_type].value}_at"


def refresh_cookie_name(subject_type: SubjectType) -> str:
    return f"nx_{SUBJECT_AUDIENCE[subject_type].value}_rt"


def refresh_cookie_path(subject_type: SubjectType) -> str:
    return f"{settings.API_V1_PREFIX}/auth/{SUBJECT_AUDIENCE[subject_type].value}"


def _secure() -> bool:
    # Local and development run on plain http://localhost, where Secure cookies are dropped.
    return settings.ENVIRONMENT in ("staging", "production")


def _seconds_until(moment: datetime) -> int:
    return max(0, int((moment - datetime.now(UTC)).total_seconds()))


def set_session_cookies(
    response: Response,
    subject_type: SubjectType,
    *,
    access_token: str,
    access_expires_at: datetime,
    refresh_token: str,
    refresh_expires_at: datetime,
) -> None:
    response.set_cookie(
        access_cookie_name(subject_type),
        access_token,
        max_age=_seconds_until(access_expires_at),
        path="/",
        secure=_secure(),
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        refresh_cookie_name(subject_type),
        refresh_token,
        max_age=_seconds_until(refresh_expires_at),
        path=refresh_cookie_path(subject_type),
        secure=_secure(),
        httponly=True,
        samesite="lax",
    )


def clear_session_cookies(response: Response, subject_type: SubjectType) -> None:
    for name, path in (
        (access_cookie_name(subject_type), "/"),
        (refresh_cookie_name(subject_type), refresh_cookie_path(subject_type)),
    ):
        response.delete_cookie(name, path=path, secure=_secure(), httponly=True, samesite="lax")


# --- counter phones -----------------------------------------------------------------------

DEVICE_COOKIE = "nx_device"
DEVICE_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # a year, renewed whenever the phone checks in


def device_cookie_path() -> str:
    """Only the display endpoints ever see the device token."""
    return f"{settings.API_V1_PREFIX}/display"


def set_device_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        DEVICE_COOKIE,
        raw_token,
        max_age=DEVICE_COOKIE_MAX_AGE,
        path=device_cookie_path(),
        secure=_secure(),
        httponly=True,
        samesite="lax",
    )
