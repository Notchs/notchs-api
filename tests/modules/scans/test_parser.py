"""Reading scans out of WhatsApp messages (table-driven)."""

import pytest

from app.modules.scans.parser import NoCode, Scan, parse
from app.modules.venues.scan_codes import scan_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hey Notchs!, get me notched. KAHWA-482913", Scan("KAHWA", "482913")),
        ("kahwa-482913", Scan("KAHWA", "482913")),
        ("KAHWA - 482913", Scan("KAHWA", "482913")),
        ("KAHWA 482913", Scan("KAHWA", "482913")),
        ("KAHWA482913", Scan("KAHWA", "482913")),
        ("KAHWA" + chr(0x2013) + "482913", Scan("KAHWA", "482913")),  # en dash
        ("KAHWA" + chr(0x2011) + "482913", Scan("KAHWA", "482913")),  # non-breaking hyphen
        ("KAHWA_482913", Scan("KAHWA", "482913")),
        ("  🙏 Kahwa-482913 thanks!!  ", Scan("KAHWA", "482913")),
        ("Hey Notchs!, get me notched.\nKAHWA2-000123", Scan("KAHWA2", "000123")),
        ("get me notched PANCAKES-123456 please", Scan("PANCAKES", "123456")),
        (scan_text("STARBUC123", "654321"), Scan("STARBUC123", "654321")),
    ],
)
def test_scans(text: str, expected: Scan) -> None:
    assert parse(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Hey Notchs!, get me notched.",
        "hey notchs get me notched",
        "GET ME NOTCHED!!!",
        "Get me notch",
        "get-me-notched 🙌",
        "Hey Notchs!, get me notched. KAHWA-4829",  # code too short: still the sentence
    ],
)
def test_the_sentence_without_a_code(text: str) -> None:
    assert parse(text) == NoCode()


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "hi",
        "LOGIN",
        "my number is 9876543210",
        "KAHWA-4829131",  # 7 digits: not a live code
        "KA-482913",  # scan codes have at least 3 characters
        "1KAHWA-482913x",
        "what is notchs?",
    ],
)
def test_not_a_scan(text: str | None) -> None:
    assert parse(text) is None
