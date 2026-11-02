"""Building WhatsApp Cloud API message bodies, with Meta's limits checked before sending.

https://developers.facebook.com/docs/whatsapp/cloud-api/messages/text-messages
https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-reply-buttons-messages
"""

from dataclasses import dataclass
from typing import Any

from app.common.phone import E164

MAX_TEXT_LENGTH = 4096
MAX_CAPTION_LENGTH = 1024
MAX_BUTTON_BODY_LENGTH = 1024
MAX_BUTTONS = 3
MAX_BUTTON_TITLE_LENGTH = 20
MAX_BUTTON_ID_LENGTH = 256
MAX_LIST_ROWS = 10
MAX_LIST_ROW_TITLE_LENGTH = 24
MAX_LIST_ROW_ID_LENGTH = 200
MAX_LIST_BUTTON_LENGTH = 20


@dataclass(frozen=True, slots=True)
class ReplyButton:
    """A tap-to-reply button. `id` comes back in the webhook when the customer taps it."""

    id: str
    title: str


@dataclass(frozen=True, slots=True)
class ListRow:
    """One choice in a list menu. `id` comes back in the webhook when the customer taps it."""

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


def image_message(phone_e164: str, link: str, caption: str) -> dict[str, Any]:
    """Meta fetches the image from `link`, which must be public and https."""
    if not link.startswith("https://"):
        raise ValueError("An image link must be https")
    if len(caption) > MAX_CAPTION_LENGTH:
        raise ValueError(f"Image captions are limited to {MAX_CAPTION_LENGTH} characters")
    return _base(phone_e164) | {"type": "image", "image": {"link": link, "caption": caption}}


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


def list_message(phone_e164: str, body: str, button: str, rows: list[ListRow]) -> dict[str, Any]:
    """A menu: `button` opens it, and each tap sends one row back."""
    if not body.strip() or len(body) > MAX_BUTTON_BODY_LENGTH:
        raise ValueError(f"List message bodies need 1 to {MAX_BUTTON_BODY_LENGTH} characters")
    if not button.strip() or len(button) > MAX_LIST_BUTTON_LENGTH:
        raise ValueError(f"List buttons need 1 to {MAX_LIST_BUTTON_LENGTH} characters")
    if not 1 <= len(rows) <= MAX_LIST_ROWS:
        raise ValueError(f"Use 1 to {MAX_LIST_ROWS} rows")
    for row in rows:
        if not row.title.strip() or len(row.title) > MAX_LIST_ROW_TITLE_LENGTH:
            raise ValueError(f"Row titles need 1 to {MAX_LIST_ROW_TITLE_LENGTH} characters")
        if not row.id or len(row.id) > MAX_LIST_ROW_ID_LENGTH:
            raise ValueError(f"Row ids need 1 to {MAX_LIST_ROW_ID_LENGTH} characters")
    if len({row.id for row in rows}) < len(rows):
        raise ValueError("Row ids must be unique")
    return _base(phone_e164) | {
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body},
            "action": {
                "button": button,
                "sections": [{"rows": [{"id": r.id, "title": r.title} for r in rows]}],
            },
        },
    }
