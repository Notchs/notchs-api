"""Venue scan codes and the QR's WhatsApp message (pure functions)."""

from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from pydantic import ValidationError

from app.modules.venues import scan_codes
from app.modules.venues.schemas import VenueUpdate


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("Kahwa House", "KAHWA"),
        ("kahwa", "KAHWA"),
        ("Café Coffee Day", "CAFE"),
        ("99 Pancakes", "PANCAKES"),
        ("The Grand Bhagwati", "THE"),
        ("Starbucks Reserve Roastery", "STARBUCK"),  # trimmed, room left for a number
        ("Q B", "VENUE"),  # nothing long enough
        ("Q Ba", "QBA"),  # short words joined
        ("चाय घर", "VENUE"),  # no Latin letters
        ("3 Idiots", "IDIOTS"),
    ],
)
def test_base_from_the_name(name: str, code: str) -> None:
    base = scan_codes.scan_code_base(name)

    assert base == code
    assert scan_codes.is_scan_code(base)


def test_numbered_codes_still_fit() -> None:
    assert scan_codes.with_number("KAHWA", 2) == "KAHWA2"
    assert scan_codes.with_number("STARBUCK", 12) == "STARBUCK12"
    assert scan_codes.with_number("STARBUCK", 123) == "STARBUC123"
    assert all(
        len(scan_codes.with_number("STARBUCK", n)) <= scan_codes.MAX_LENGTH for n in range(2, 60)
    )


def test_random_codes_are_valid_and_different() -> None:
    codes = {scan_codes.random_scan_code() for _ in range(50)}

    assert len(codes) == 50
    assert all(scan_codes.is_scan_code(code) for code in codes)


@pytest.mark.parametrize("code", ["KA", "1KAHWA", "KAHWA-SBR", "KAHWAHOUSE1", "kahwa", ""])
def test_invalid_codes(code: str) -> None:
    assert not scan_codes.is_scan_code(code)


def test_the_message_a_customer_sends() -> None:
    assert scan_codes.scan_text("KAHWA", "482913") == "Hey Notchs!, get me notched. KAHWA-482913"


def test_link_template_opens_whatsapp_with_the_message() -> None:
    template = scan_codes.scan_link_template("15556755530", "KAHWA")

    assert template.startswith("https://wa.me/15556755530?text=")
    assert template.endswith("{code}")
    link = template.replace("{code}", "482913")
    text = parse_qs(urlsplit(link).query)["text"][0]
    assert text == "Hey Notchs!, get me notched. KAHWA-482913"
    assert " " not in link and "," not in link and "!" not in unquote(urlsplit(link).path)


@pytest.mark.parametrize(("sent", "stored"), [("kahwa", "KAHWA"), ("  Kahwa2 ", "KAHWA2")])
def test_admins_may_type_any_case(sent: str, stored: str) -> None:
    assert VenueUpdate(scan_code=sent).scan_code == stored


@pytest.mark.parametrize("sent", ["KA", "KAHWA-SBR", "1KAHWA", "KAHWAHOUSE1"])
def test_bad_codes_are_refused(sent: str) -> None:
    with pytest.raises(ValidationError):
        VenueUpdate(scan_code=sent)


def test_a_scan_code_cannot_be_removed() -> None:
    with pytest.raises(ValidationError):
        VenueUpdate(scan_code=None)
