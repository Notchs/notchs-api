"""The live code check (TOTP) with frozen clocks."""

import pytest

from app.modules.scans import verifier
from app.modules.venues.counters import new_totp_secret

# RFC 6238 appendix B: secret "12345678901234567890" (ASCII), SHA-1, 30-second steps. The RFC lists
# 8-digit codes; 6-digit codes are their last 6 digits.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
RFC_VECTORS = [
    (59, "287082"),
    (1111111109, "081804"),
    (1111111111, "050471"),
    (1234567890, "005924"),
    (2000000000, "279037"),
    (20000000000, "353130"),
]


@pytest.mark.parametrize(("timestamp", "code"), RFC_VECTORS)
def test_codes_match_the_standard(timestamp: int, code: str) -> None:
    assert verifier.code_at(RFC_SECRET, verifier.slot_of(timestamp, 30)) == code


@pytest.mark.parametrize(("timestamp", "code"), RFC_VECTORS)
def test_the_current_code_is_accepted(timestamp: int, code: str) -> None:
    assert verifier.verify(RFC_SECRET, code, interval=30, now=timestamp) == timestamp // 30


def test_a_minute_late_is_fine_but_not_more() -> None:
    shown_at = 1_800_000_000  # the phone showed this code at the start of its slot
    code = verifier.code_at(RFC_SECRET, verifier.slot_of(shown_at, 30))

    assert verifier.verify(RFC_SECRET, code, interval=30, now=shown_at + 30) is not None
    assert verifier.verify(RFC_SECRET, code, interval=30, now=shown_at + 89) is not None
    assert verifier.verify(RFC_SECRET, code, interval=30, now=shown_at + 90) is None  # 3 slots


def test_a_phone_running_fast_is_fine() -> None:
    now = 1_800_000_000
    next_code = verifier.code_at(RFC_SECRET, verifier.slot_of(now, 30) + 1)
    two_ahead = verifier.code_at(RFC_SECRET, verifier.slot_of(now, 30) + 2)

    assert verifier.verify(RFC_SECRET, next_code, interval=30, now=now) == now // 30 + 1
    assert verifier.verify(RFC_SECRET, two_ahead, interval=30, now=now) is None


def test_the_matching_slot_is_returned() -> None:
    now = 1_800_000_015
    previous = verifier.code_at(RFC_SECRET, now // 30 - 1)

    assert verifier.verify(RFC_SECRET, previous, interval=30, now=now) == now // 30 - 1


@pytest.mark.parametrize("code", ["000000", "12345", "1234567", "abcdef", "", " 28708"])
def test_wrong_codes(code: str) -> None:
    assert verifier.verify(RFC_SECRET, code, interval=30, now=59) is None


def test_another_venues_secret_does_not_match() -> None:
    mine, theirs = new_totp_secret(), new_totp_secret()
    code = verifier.code_at(theirs, verifier.slot_of(1_800_000_000, 30))

    assert verifier.verify(mine, code, interval=30, now=1_800_000_000) is None


def test_secrets_from_pairing_and_longer_intervals() -> None:
    secret = new_totp_secret()  # base32 without padding, as stored for counter phones
    now = 1_800_000_000
    code = verifier.code_at(secret, verifier.slot_of(now, 60))

    assert verifier.verify(secret, code, interval=60, now=now + 100) == now // 60
    assert verifier.verify(secret.lower(), code, interval=60, now=now) == now // 60
