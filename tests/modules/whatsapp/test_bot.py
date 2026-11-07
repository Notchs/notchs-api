"""The bot's pure rules (no database)."""

import pytest

from app.common.phone import masked_phone
from app.modules.whatsapp.bot import LOGIN_BUTTON, LOGIN_MESSAGE, is_login
from app.modules.whatsapp.models import WhatsAppMessage


def incoming(body: str | None, kind: str = "text", button_id: str | None = None) -> WhatsAppMessage:
    payload = {}
    if button_id is not None:  # stored as the webhook stores it
        payload = {
            "message": {
                "type": "interactive",
                "interactive": {
                    "type": "button_reply",
                    "button_reply": {"id": button_id, "title": body},
                },
            }
        }
    return WhatsAppMessage(
        direction="in", phone_e164="+919876543210", message_type=kind, body=body, payload=payload
    )


def test_tapping_the_login_button() -> None:
    assert is_login(incoming(LOGIN_BUTTON.title, "button", LOGIN_BUTTON.id))


def test_tapping_another_button_is_not_login() -> None:
    assert not is_login(incoming("Matcha", "button", "tag:matcha"))


def test_login_button_fits_whatsapps_limit() -> None:
    assert len(LOGIN_BUTTON.title) <= 20


@pytest.mark.parametrize(
    ("body", "kind"),
    [("LOGIN", "text"), ("login", "text"), ("  Login\n", "text"), ("LOGIN", "button")],
)
def test_login(body: str, kind: str) -> None:
    assert is_login(incoming(body, kind))


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ("LOGIN please", "text"),
        ("LOG IN", "text"),
        ("", "text"),
        (None, "text"),
        ("LOGIN", "image"),  # a caption
        (None, "other"),
    ],
)
def test_not_login(body: str | None, kind: str) -> None:
    assert not is_login(incoming(body, kind))


@pytest.mark.parametrize(
    "body",
    [
        LOGIN_MESSAGE,
        "hey notchs, i want to login",
        "Hey Notchs! I want to log in.",
        "  HEY   NOTCHS,  I WANT TO LOGIN \n",
        "Hi Notchs, I want to login",
        "Notchs, I want to login",
    ],
)
def test_the_websites_login_message(body: str) -> None:
    assert is_login(incoming(body))


@pytest.mark.parametrize(
    "body",
    [
        "Hey Notchs, I want to login and also my last visit was not counted",
        "Hey Notchs, I don't want to login",
        "I want to login",  # without "Notchs" it could be about anything
        "Hey Notchs!, get me notched.",  # the scan sentence
        "Hey Notchs!, get me notched. KAHWA-482913",
    ],
)
def test_other_sentences_are_not_login(body: str) -> None:
    assert not is_login(incoming(body))


def test_phone_numbers_are_masked_in_logs() -> None:
    assert masked_phone("+919876543210") == "…3210"
