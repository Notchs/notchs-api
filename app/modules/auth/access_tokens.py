"""Short-lived access tokens: signed JWTs carried in the session cookie.

Named `access_tokens` rather than `jwt` so it never shadows the PyJWT package.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import settings
from app.modules.auth.enums import SubjectType
from app.modules.auth.exceptions import NotAuthenticatedException

_REQUIRED_CLAIMS = ["exp", "iat", "iss", "sub", "typ"]


@dataclass(frozen=True, slots=True)
class AccessClaims:
    subject_type: SubjectType
    subject_id: uuid.UUID
    expires_at: datetime
    # Set for venue users, so venue routes need no extra lookup to know the venue.
    venue_id: uuid.UUID | None = None


def create_access_token(
    subject_type: SubjectType,
    subject_id: uuid.UUID,
    *,
    venue_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> str:
    issued_at = now or datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": settings.JWT_ISSUER,
        "sub": str(subject_id),
        "typ": subject_type.value,
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=settings.ACCESS_TOKEN_TTL_MINUTES),
    }
    if venue_id is not None:
        claims["vid"] = str(venue_id)
    return jwt.encode(
        claims, settings.JWT_SECRET.get_secret_value(), algorithm=settings.JWT_ALGORITHM
    )


def decode_access_token(token: str, *, expected_type: SubjectType) -> AccessClaims:
    """Verify signature, expiry, issuer and subject type. Raises NotAuthenticated otherwise."""
    try:
        claims = jwt.decode(
            token,
            settings.JWT_SECRET.get_secret_value(),
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER,
            options={"require": _REQUIRED_CLAIMS},
        )
        if claims["typ"] != expected_type.value:
            raise NotAuthenticatedException()
        return AccessClaims(
            subject_type=expected_type,
            subject_id=uuid.UUID(claims["sub"]),
            expires_at=datetime.fromtimestamp(claims["exp"], UTC),
            venue_id=uuid.UUID(claims["vid"]) if "vid" in claims else None,
        )
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise NotAuthenticatedException() from exc
