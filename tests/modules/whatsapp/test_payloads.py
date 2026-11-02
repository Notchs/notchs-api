import pytest

from app.modules.whatsapp.payloads import (
    MAX_TEXT_LENGTH,
    ReplyButton,
    buttons_message,
    text_message,
)

PHONE = "+919876543210"


def test_text_message() -> None:
    assert text_message(PHONE, "Stamp added at Kahwa!") == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "919876543210",
        "type": "text",
        "text": {"body": "Stamp added at Kahwa!", "preview_url": False},
    }


def test_text_message_with_link_preview() -> None:
    assert text_message(PHONE, "https://notchs.com", preview_url=True)["text"]["preview_url"]


@pytest.mark.parametrize("body", ["", "   ", "x" * (MAX_TEXT_LENGTH + 1)])
def test_text_message_rejects_bad_bodies(body: str) -> None:
    with pytest.raises(ValueError):
        text_message(PHONE, body)


@pytest.mark.parametrize("phone", ["9876543210", "919876543210", "+0123", "phone"])
def test_rejects_numbers_that_are_not_e164(phone: str) -> None:
    with pytest.raises(ValueError, match=r"E.164"):
        text_message(phone, "hi")


def test_buttons_message() -> None:
    message = buttons_message(
        PHONE,
        "What should we call you?",
        [ReplyButton("name:yes", "Use Riya"), ReplyButton("name:other", "Type another")],
    )

    assert message["type"] == "interactive"
    assert message["interactive"] == {
        "type": "button",
        "body": {"text": "What should we call you?"},
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": "name:yes", "title": "Use Riya"}},
                {"type": "reply", "reply": {"id": "name:other", "title": "Type another"}},
            ]
        },
    }


@pytest.mark.parametrize(
    "buttons",
    [
        [],
        [ReplyButton(f"b{i}", f"B{i}") for i in range(4)],
        [ReplyButton("a", "x" * 21)],
        [ReplyButton("a", "  ")],
        [ReplyButton("", "Title")],
        [ReplyButton("a", "One"), ReplyButton("a", "Two")],
        [ReplyButton("a", "Same"), ReplyButton("b", "Same")],
    ],
    ids=["none", "four", "long-title", "blank-title", "no-id", "duplicate-id", "duplicate-title"],
)
def test_buttons_message_enforces_meta_limits(buttons: list[ReplyButton]) -> None:
    with pytest.raises(ValueError):
        buttons_message(PHONE, "Pick one", buttons)


def test_buttons_message_body_limit() -> None:
    with pytest.raises(ValueError):
        buttons_message(PHONE, "x" * 1025, [ReplyButton("a", "OK")])
