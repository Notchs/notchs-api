"""Encrypting small secrets that must be readable again (unlike passwords, which are hashed).

Uses Fernet (AES + HMAC) with TOTP_ENCRYPTION_KEY. Today this protects counter phones' TOTP
secrets; anything else that needs reversible encryption can use the same helpers.
"""

from functools import cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class DecryptionError(Exception):
    """The value was not encrypted with the current key, or was changed."""


@cache
def _fernet() -> Fernet:
    return Fernet(settings.TOTP_ENCRYPTION_KEY.get_secret_value())


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionError("Could not decrypt: wrong key or damaged value") from exc
