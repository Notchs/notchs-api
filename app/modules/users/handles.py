"""Customer names and handles.

The name comes from the customer's WhatsApp profile ("Riya Mehta") and is what the leaderboard
shows; it need not be unique. Every customer also gets a unique handle, made automatically from
the name with a random number: `riya.m4821`. Handles tell customers apart (profile links, support,
search); customers may change theirs on the website later.
"""

import re
import secrets
import unicodedata
from collections.abc import Awaitable, Callable

MAX_NAME_LENGTH = 60  # users.name
MAX_HANDLE_LENGTH = 30  # users.handle
HANDLE_REGEX = r"^[a-z0-9._]{3,30}$"
FALLBACK_BASE = "notcher"
MAX_BASE_LENGTH = 15
SHORT_DIGITS, LONG_DIGITS = 4, 6
SHORT_TRIES = 8

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SPACES = re.compile(r"\s+")
_LETTERS = re.compile(r"[a-z]+")


def clean_name(profile_name: str | None) -> str | None:
    """A WhatsApp profile name made safe to store and show: no control characters, single spaces,
    at most 60 characters. None if nothing is left."""
    if not profile_name:
        return None
    # Line breaks and tabs become spaces first; other control characters are dropped.
    name = _SPACES.sub(" ", _CONTROL.sub("", _SPACES.sub(" ", profile_name))).strip()
    return name[:MAX_NAME_LENGTH].rstrip() or None


def handle_base(name: str | None) -> str:
    """`Riya Mehta` -> `riya.m`, `Riya` -> `riya`, `O'Brien Shah` -> `obrien.s`, `J Shah` -> `shah`,
    `Rīyā Mēhtā` -> `riya.m`, `રિયા` -> `notcher`. The first word of 2+ letters, then the first
    letter of the last word."""
    if not name:
        return FALLBACK_BASE
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    words = _LETTERS.findall(ascii_name.replace("'", ""))
    first_index = next((i for i, word in enumerate(words) if len(word) >= 2), None)
    if first_index is None:
        return FALLBACK_BASE
    first = words[first_index][:MAX_BASE_LENGTH]
    return f"{first}.{words[-1][0]}" if first_index < len(words) - 1 else first


def random_handle(base: str = FALLBACK_BASE, digits: int = LONG_DIGITS) -> str:
    number = secrets.randbelow(9 * 10 ** (digits - 1)) + 10 ** (digits - 1)  # no leading zero
    return f"{base}{number}"


async def free_handle(name: str | None, *, taken: Callable[[str], Awaitable[bool]]) -> str:
    """A handle nobody has yet: `riya.m4821`, or with 6 digits if many `riya.m` exist.

    `taken` checks the database (`UserRepository.handle_exists`).
    """
    base = handle_base(name)
    for attempt in range(SHORT_TRIES + 1):
        candidate = random_handle(base, SHORT_DIGITS if attempt < SHORT_TRIES else LONG_DIGITS)
        if not await taken(candidate):
            return candidate
    return candidate  # 6 digits: a clash is all but impossible; the unique index guards anyway
