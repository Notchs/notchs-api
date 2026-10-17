"""Building WhatsApp Cloud API message bodies, with Meta's limits checked before sending.

https://developers.facebook.com/docs/whatsapp/cloud-api/messages/text-messages
https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-reply-buttons-messages
"""

from dataclasses import dataclass
from typing import Any

from app.common.phone import E164

MAX_TEXT_LENGTH = 4096
MAX_BUTTON_BODY_LENGTH = 1024
MAX_BUTTONS = 3
MAX_BUTTON_TITLE_LENGTH = 20
MAX_BUTTON_ID_LENGTH = 256


@dataclass(frozen=True, slots=True)
class ReplyButton:
    """A tap-to-reply button. `id` comes back in the webhook when the customer taps it."""

    id: str
    title: str


def _recipient(phone_e164: str) -> str:
    """Meta takes the number without the leading `+`."""
    if not E164.fullmatch(phone_e164):
        raise ValueError(f"Not an E.164 phone number: {phone_e164!r}")
    return phone_e164.removeprefix("+")


def _base(phone_e164: str) -> dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _recipient(phone_e164),
    }


def text_message(phone_e164: str, body: str, *, preview_url: bool = False) -> dict[str, Any]:
    if not body.strip():
        raise ValueError("A text message needs a body")
    if len(body) > MAX_TEXT_LENGTH:
        raise ValueError(f"Text messages are limited to {MAX_TEXT_LENGTH} characters")
    return _base(phone_e164) | {
        "type": "text",
        "text": {"body": body, "preview_url": preview_url},
    }


def buttons_message(phone_e164: str, body: str, buttons: list[ReplyButton]) -> dict[str, Any]:
    if not body.strip():
        raise ValueError("A button message needs a body")
    if len(body) > MAX_BUTTON_BODY_LENGTH:
        raise ValueError(
            f"Button message bodies are limited to {MAX_BUTTON_BODY_LENGTH} characters"
        )
    if not 1 <= len(buttons) <= MAX_BUTTONS:
        raise ValueError(f"Use 1 to {MAX_BUTTONS} buttons")
    for button in buttons:
        if not button.title.strip() or len(button.title) > MAX_BUTTON_TITLE_LENGTH:
            raise ValueError(f"Button titles need 1 to {MAX_BUTTON_TITLE_LENGTH} characters")
        if not button.id or len(button.id) > MAX_BUTTON_ID_LENGTH:
            raise ValueError(f"Button ids need 1 to {MAX_BUTTON_ID_LENGTH} characters")
    ids, titles = {b.id for b in buttons}, {b.title for b in buttons}
    if len(ids) < len(buttons) or len(titles) < len(buttons):
        raise ValueError("Button ids and titles must be unique")
    return _base(phone_e164) | {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b.id, "title": b.title}} for b in buttons
                ]
            },
        },
    }
