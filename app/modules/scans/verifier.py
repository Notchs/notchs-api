"""Checking the live 6-digit code a counter phone shows (TOTP, RFC 6238: HMAC-SHA1, 6 digits).

The phone makes its codes offline from a secret it got at pairing (counter_devices.totp_secret_enc)
and a new one every `interval` seconds (30 by default). A code is accepted if it belongs to:

- the current time slot,
- the 2 slots before it: a customer may take a minute to press Send,
- 1 slot after it: the phone's clock may run a few seconds fast.

The matching slot number is returned: the scan check uses it so one code is not used by many
people (step 4).
"""

import base64
import hashlib
import hmac
import struct
import time

DIGITS = 6
SLOTS_BEFORE = 2
SLOTS_AFTER = 1
# How far back a refused code is still recognised as an old real one ("expired", not "wrong").
EXPIRED_LOOKBACK_SECONDS = 60 * 60


def _key(secret_b32: str) -> bytes:
    """Base32 secrets are stored without '=' padding (counters.new_totp_secret)."""
    secret = secret_b32.strip().upper()
    return base64.b32decode(secret + "=" * (-len(secret) % 8))


def code_at(secret_b32: str, slot: int, *, digits: int = DIGITS) -> str:
    """The code for one time slot (RFC 4226 dynamic truncation)."""
    digest = hmac.new(_key(secret_b32), struct.pack(">Q", slot), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**digits).zfill(digits)


def slot_of(timestamp: float, interval: int) -> int:
    return int(timestamp) // interval


def verify(secret_b32: str, code: str, *, interval: int, now: float | None = None) -> int | None:
    """The time slot `code` belongs to, or None if it is wrong or too old."""
    if len(code) != DIGITS or not code.isdigit():
        return None
    current = slot_of(time.time() if now is None else now, interval)
    # Newest first, and every candidate compared in constant time.
    matched = None
    for slot in range(current + SLOTS_AFTER, max(current - SLOTS_BEFORE, 0) - 1, -1):
        if hmac.compare_digest(code_at(secret_b32, slot), code) and matched is None:
            matched = slot
    return matched


def is_expired(secret_b32: str, code: str, *, interval: int, now: float | None = None) -> bool:
    """True if `code` is a real code from the last EXPIRED_LOOKBACK_SECONDS that `verify` no longer
    accepts, so the customer can be told to scan again rather than that the code is wrong."""
    if len(code) != DIGITS or not code.isdigit():
        return False
    current = slot_of(time.time() if now is None else now, interval)
    newest = current - SLOTS_BEFORE - 1
    oldest = max(current - EXPIRED_LOOKBACK_SECONDS // interval, 0)
    found = False
    for slot in range(newest, oldest - 1, -1):
        found |= hmac.compare_digest(code_at(secret_b32, slot), code)
    return found
