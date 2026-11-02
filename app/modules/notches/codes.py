"""The counter code and the WhatsApp message it travels in. Pure functions, no database.

The counter phone shows a QR that opens WhatsApp with this message typed:

    Notch me at Kahwa House · 482913 [VEN-7K3M9QX2]

    "Notch me at …"   the human part, so the customer sees what they are sending
    482913            the rotating code: RFC 6238 TOTP (SHA1, 6 digits, 30 s), made offline on
                      the phone from the secret it got at pairing (GET /display/config)
    [VEN-7K3M9QX2]    the venue, so the server knows which secret to check the code against

The design's QR spec drew the code as letters (K7Q-29F) and the venue as a short ref (v07).
The counter API was already built on standard TOTP, which every platform can generate, so
the code is 6 digits and the venue ref is its public code. Parsing is lenient about spacing,
case and the words around the code, so a customer who edits the text a little still notches.
"""

import base64
import hashlib
import hmac
import re
import struct
from dataclasses import dataclass
from urllib.parse import quote

TOTP_DIGITS = 6
MESSAGE_PREFIX = "Notch me at"
CODE_PLACEHOLDER = "{code}"

_VENUE_REF = re.compile(r"\[?\s*(VEN)\s*-\s*([0-9A-HJKMNP-TV-Z]{8})\s*\]?", re.IGNORECASE)
# Six digits, optionally split in two (482 913, 482-913), not part of a longer number.
_CODE = re.compile(r"(?<![0-9])([0-9]{3})[\s\-]?([0-9]{3})(?![0-9])")


@dataclass(frozen=True, slots=True)
class ScanMessage:
    venue_code: str  # VEN-7K3M9QX2
    code: str  # 482913


def parse_scan(text: str | None) -> ScanMessage | None:
    """The venue and code in a customer's message, or None if it is not a counter scan."""
    if not text:
        return None
    ref = _VENUE_REF.search(text)
    if ref is None:
        return None
    # Look for the code outside the venue ref: its 8 characters may contain digits.
    rest = text[: ref.start()] + " " + text[ref.end() :]
    code = _CODE.search(rest)
    if code is None:
        return None
    return ScanMessage(
        venue_code=f"{ref.group(1)}-{ref.group(2)}".upper(), code=code.group(1) + code.group(2)
    )


def message_template(venue_name: str, venue_code: str) -> str:
    """What the counter QR pre-types, with `{code}` where the phone puts the current code."""
    name = " ".join(venue_name.split())  # one line, single spaces
    return f"{MESSAGE_PREFIX} {name} · {CODE_PLACEHOLDER} [{venue_code}]"


def whatsapp_link(public_number: str, message: str) -> str:
    """wa.me link that opens a chat with Notchs and the message typed."""
    return f"https://wa.me/{public_number.removeprefix('+')}?text={quote(message, safe='')}"


def _secret_bytes(secret_b32: str) -> bytes:
    cleaned = secret_b32.strip().replace(" ", "").upper()
    return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))


def totp(secret_b32: str, window: int, *, digits: int = TOTP_DIGITS) -> str:
    """RFC 6238 / 4226: the code for one time step (`window` = unix seconds // interval)."""
    digest = hmac.new(_secret_bytes(secret_b32), struct.pack(">Q", window), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def window_of(unix_seconds: float, interval_s: int) -> int:
    return int(unix_seconds // interval_s)


def match_offset(
    secret_b32: str, code: str, scan_window: int, offsets: range | tuple[int, ...]
) -> int | None:
    """Which window (relative to `scan_window`) the code belongs to, trying `offsets` in order;
    None if it matches none of them. Constant-time per comparison."""
    for offset in offsets:
        if hmac.compare_digest(totp(secret_b32, scan_window + offset), code):
            return offset
    return None
