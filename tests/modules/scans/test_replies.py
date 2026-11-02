"""The bot's scan replies, and the image message they can use."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.modules.scans import replies
from app.modules.scans.models import RejectReason
from app.modules.users.consent import CONSENT_VERSION, consent_text
from app.modules.whatsapp import payloads
from app.modules.whatsapp.bot import CONSENT_BUTTON
from app.modules.whatsapp.payloads import MAX_BUTTON_BODY_LENGTH, MAX_BUTTON_TITLE_LENGTH

PHONE = "+919876543210"


def test_stamp_with_a_link_keeps_it_on_the_last_line() -> None:
    text = replies.stamp_text("Kahwa House", 145, 12, "https://notchs.example/l?token=abc")

    assert text.startswith("Stamp added at Kahwa House! Notch #0145. +12 points.")
    assert text.rsplit("\n", 1)[1] == "https://notchs.example/l?token=abc"


def test_stamp_without_a_link_says_how_to_get_one() -> None:
    assert "Send LOGIN" in replies.stamp_text("Kahwa House", 1, 10, None)


def test_stamp_before_agreeing_does_not_mention_login() -> None:
    text = replies.stamp_text("Kahwa House", 1, 12, None, "You're #1 in Ahmedabad.", agreed=False)

    assert text == "Stamp added at Kahwa House! Notch #0001. +12 points.\nYou're #1 in Ahmedabad."


def test_no_code_never_promises_a_stamp() -> None:
    assert "Stamp added" not in replies.no_code_text(None)
    assert replies.no_code_text("https://x.example/l").endswith("\nhttps://x.example/l")


@pytest.mark.parametrize("reason", [r for r in RejectReason if r != RejectReason.BLOCKED_CUSTOMER])
def test_every_refusal_has_words(reason: RejectReason) -> None:
    assert replies.rejection_text(reason, "Kahwa House")


def test_blocked_customers_are_not_answered() -> None:
    assert replies.rejection_text(RejectReason.BLOCKED_CUSTOMER, "Kahwa House") is None


def test_already_today_without_a_venue_name() -> None:
    text = replies.rejection_text(RejectReason.ALREADY_TODAY, None)

    assert text is not None and "this venue" in text


def test_consent_fits_whatsapps_limits() -> None:
    longest = f"{replies.SCAN_AT_VENUE}\n\n{consent_text()}"

    assert len(longest) <= MAX_BUTTON_BODY_LENGTH
    assert len(CONSENT_BUTTON.title) <= MAX_BUTTON_TITLE_LENGTH
    assert len(CONSENT_VERSION) <= 20  # users.consent_version
    assert "18" in consent_text() and "/privacy" in consent_text()


def test_image_message() -> None:
    payload = payloads.image_message(PHONE, "https://x.example/s.png", "Stamp")

    assert payload["to"] == PHONE.removeprefix("+")
    assert payload["type"] == "image"
    assert payload["image"] == {"link": "https://x.example/s.png", "caption": "Stamp"}


def test_image_link_must_be_https() -> None:
    with pytest.raises(ValueError, match="https"):
        payloads.image_message(PHONE, "http://x.example/s.png", "Stamp")


def test_image_caption_length() -> None:
    with pytest.raises(ValueError, match="captions"):
        payloads.image_message(PHONE, "https://x.example/s.png", "x" * 1025)


def test_stamp_image_setting_must_be_https() -> None:
    with pytest.raises(ValidationError, match="WHATSAPP_STAMP_IMAGE_URL"):
        Settings(_env_file=None, WHATSAPP_STAMP_IMAGE_URL="http://x.example/s.png")


def test_stamp_image_setting_is_optional() -> None:
    assert Settings(_env_file=None, WHATSAPP_STAMP_IMAGE_URL="").WHATSAPP_STAMP_IMAGE_URL == ""
