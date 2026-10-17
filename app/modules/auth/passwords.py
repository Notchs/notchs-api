"""Password hashing (argon2id) and the password policy.

Hashing is deliberately slow (tens of milliseconds of CPU), so async code uses the
`*_async` helpers, which run it in a worker thread instead of blocking the event loop.
"""

import asyncio
from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.modules.auth.exceptions import PasswordInvalidException

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128

# argon2id with the library's recommended parameters.
_hasher = PasswordHasher()


def check_password_policy(password: str, *, email: str | None = None) -> None:
    """10-128 characters and not the email address. No composition rules."""
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise PasswordInvalidException(
            f"Use {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} characters."
        )
    if email is not None and password.strip().lower() == email.strip().lower():
        raise PasswordInvalidException("Your password cannot be your email address.")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError, InvalidHashError:  # VerifyMismatchError is a VerificationError
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the hash was made with older parameters and should be replaced on login."""
    return _hasher.check_needs_rehash(password_hash)


@cache
def _dummy_hash() -> str:
    return _hasher.hash("dummy password used to equalise login timing")


def verify_dummy(password: str) -> None:
    """Spend the same time as a real check, so a login for an unknown email is not faster."""
    verify_password(_dummy_hash(), password)


async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password_hash: str, password: str) -> bool:
    return await asyncio.to_thread(verify_password, password_hash, password)


async def verify_dummy_async(password: str) -> None:
    await asyncio.to_thread(verify_dummy, password)
