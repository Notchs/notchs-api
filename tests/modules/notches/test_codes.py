import base64
from urllib.parse import parse_qs, urlparse

import pytest

from app.modules.notches.codes import (
    CODE_PLACEHOLDER,
    match_offset,
    message_template,
    parse_scan,
    totp,
    whatsapp_link,
)

# RFC 6238 appendix B: SHA1 seed "12345678901234567890", 30 s steps, 8-digit codes. The
# 6-digit code is the last 6 digits of the 8-digit one.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


@pytest.mark.parametrize(
    ("unix_time", "eight_digits"),
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_totp_matches_rfc_6238(unix_time: int, eight_digits: str) -> None:
    assert totp(RFC_SECRET, unix_time // 30, digits=8) == eight_digits
    assert totp(RFC_SECRET, unix_time // 30) == eight_digits[-6:]


def test_totp_accepts_unpadded_lowercase_secrets() -> None:
    assert totp(RFC_SECRET.rstrip("=").lower(), 1) == totp(RFC_SECRET, 1)


def test_match_offset_finds_the_window_and_respects_the_allowed_offsets() -> None:
    code = totp(RFC_SECRET, 100)
    assert match_offset(RFC_SECRET, code, 101, (0, -1, 1)) == -1
    assert match_offset(RFC_SECRET, code, 102, (0, -1, 1)) is None
    assert match_offset(RFC_SECRET, code, 102, (0, -1, 1, -2, 2)) == -2


@pytest.mark.parametrize(
    "text",
    [
        "Notch me at Kahwa House · 482913 [VEN-7K3M9QX2]",
        "notch me at kahwa house · 482 913 [ven-7k3m9qx2]",
        "482-913 VEN-7K3M9QX2",
        "Notch me at Café 24/7 · 482913 [VEN-7K3M9QX2]",
        "[VEN-7K3M9QX2] 482913",
    ],
)
def test_parse_scan_reads_the_venue_and_code(text: str) -> None:
    scan = parse_scan(text)

    assert scan is not None
    assert scan.venue_code == "VEN-7K3M9QX2"
    assert scan.code == "482913"


def test_parse_scan_ignores_digits_inside_the_venue_code() -> None:
    # VEN-123456AB has six digits in a row; they must not be read as the code.
    assert parse_scan("Notch me at X [VEN-123456AB]") is None
    scan = parse_scan("Notch me at X · 000111 [VEN-123456AB]")
    assert scan is not None and scan.code == "000111"


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "LOGIN",
        "hi, how does this work?",
        "482913",
        "VEN-7K3M9QX2",
        "1234567 [VEN-7K3M9QX2]",
    ],
)
def test_parse_scan_returns_none_for_other_messages(text: str | None) -> None:
    assert parse_scan(text) is None


def test_message_template_round_trips_through_the_parser() -> None:
    template = message_template("  Kahwa\n House ", "VEN-7K3M9QX2")

    assert template == f"Notch me at Kahwa House · {CODE_PLACEHOLDER} [VEN-7K3M9QX2]"
    scan = parse_scan(template.replace(CODE_PLACEHOLDER, "123456"))
    assert scan is not None and (scan.venue_code, scan.code) == ("VEN-7K3M9QX2", "123456")


def test_whatsapp_link_encodes_the_message() -> None:
    link = whatsapp_link("+919099012345", "Notch me at Kahwa House · 123456 [VEN-7K3M9QX2]")
    parsed = urlparse(link)

    assert parsed.netloc == "wa.me"
    assert parsed.path == "/919099012345"
    assert parse_qs(parsed.query)["text"] == ["Notch me at Kahwa House · 123456 [VEN-7K3M9QX2]"]
