"""Opaque one-time and refresh tokens.

The raw token goes to the user (in a link or a cookie) exactly once. Only its SHA-256 hash is
stored, so a database leak does not hand out working links or sessions.
"""

import hashlib
import secrets
from datetime import timedelta

from app.core.config import settings
from app.modules.auth.enums import TokenType

TOKEN_BYTES = 32  # 256 bits of randomness

_FIXED_LIFETIMES = {
    TokenType.MAGIC_LINK: timedelta(minutes=15),
    TokenType.DEVICE_PAIRING: timedelta(minutes=10),
    TokenType.SET_PASSWORD: timedelta(hours=72),
    TokenType.PASSWORD_RESET: timedelta(minutes=30),
}


def new_token() -> str:
    """URL-safe random token, about 43 characters."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def lifetime(token_type: TokenType) -> timedelta:
    if token_type is TokenType.REFRESH:
        return timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS)
    return _FIXED_LIFETIMES[token_type]
