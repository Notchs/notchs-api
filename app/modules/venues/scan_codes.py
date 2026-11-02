"""Venue scan codes and the WhatsApp message a counter QR pre-types.

A customer scans the QR on the counter phone; WhatsApp opens with

    Hey Notchs!, get me notched. KAHWA-482913

`KAHWA` is the venue's scan code (made from its name, unique) and `482913` the live code the counter
phone shows (TOTP, changes every interval). The bot reads the code and ignores the words.

Scan codes: 3 to 10 capital letters and digits, starting with a letter, no hyphen (the hyphen
separates the live code). Made from the venue's first word, `KAHWA`, then `KAHWA2`, `KAHWA3`...
"""

import re
import secrets
import unicodedata
from urllib.parse import quote

from app.common.ids import CROCKFORD_ALPHABET

SCAN_MESSAGE = "Hey Notchs!, get me notched."
MIN_LENGTH, MAX_LENGTH = 3, 10
SCAN_CODE_REGEX = rf"^[A-Z][A-Z0-9]{{{MIN_LENGTH - 1},{MAX_LENGTH - 1}}}$"
FALLBACK_BASE = "VENUE"
# Room left for a number when the base is taken (KAHWAHOUS -> KAHWAHOU12).
BASE_LENGTH = MAX_LENGTH - 2
LIVE_CODE_PLACEHOLDER = "{code}"

_WORDS = re.compile(r"[A-Za-z0-9]+")
_LETTERS_FIRST = re.compile(r"^[0-9]+")


def scan_code_base(name: str) -> str:
    """`Kahwa House` -> `KAHWA`, `Café Coffee Day` -> `CAFE`, `99 Pancakes` -> `PANCAKES`.

    The first word with at least 3 characters once leading digits are dropped; otherwise the words
    joined; otherwise `VENUE`.
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = [_LETTERS_FIRST.sub("", word).upper() for word in _WORDS.findall(ascii_name)]
    words = [word for word in words if word]
    for word in words:
        if len(word) >= MIN_LENGTH:
            return word[:BASE_LENGTH]
    joined = "".join(words)
    return joined[:BASE_LENGTH] if len(joined) >= MIN_LENGTH else FALLBACK_BASE


def with_number(base: str, number: int) -> str:
    """`KAHWA`, 2 -> `KAHWA2`, trimming the base so the result still fits."""
    suffix = str(number)
    return base[: MAX_LENGTH - len(suffix)] + suffix


def random_scan_code() -> str:
    """For venues made without a name-based code (tests, scripts): `V` + 7 random characters."""
    return "V" + "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(MAX_LENGTH - 3))


def normalize_scan_code(value: str) -> str:
    return value.strip().upper()


def is_scan_code(value: str) -> bool:
    return re.fullmatch(SCAN_CODE_REGEX, value) is not None


def scan_text(scan_code: str, live_code: str) -> str:
    """The message a QR pre-types: `Hey Notchs!, get me notched. KAHWA-482913`."""
    return f"{SCAN_MESSAGE} {scan_code}-{live_code}"


def scan_link_template(whatsapp_number: str, scan_code: str) -> str:
    """The QR's WhatsApp link with `{code}` where the counter phone puts its live code, e.g.

    https://wa.me/15556755530?text=Hey%20Notchs%21%2C%20get%20me%20notched.%20KAHWA-{code}
    """
    text = quote(f"{SCAN_MESSAGE} {scan_code}-", safe="")
    return f"https://wa.me/{whatsapp_number}?text={text}{LIVE_CODE_PLACEHOLDER}"
