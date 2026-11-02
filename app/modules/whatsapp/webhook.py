"""Reading Meta's webhook: checking the signature and turning the payload into simple records.

Pure functions only (no database), so every Meta payload shape can be tested directly.
https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples
"""

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any

from app.common.phone import E164
from app.core.logging import get_logger
from app.modules.whatsapp.models import MessageStatus, MessageType

logger = get_logger(__name__)

SIGNATURE_PREFIX = "sha256="


def signature_is_valid(raw_body: bytes, header: str | None, app_secret: str) -> bool:
    """`X-Hub-Signature-256: sha256=<hex HMAC-SHA256 of the raw body with the app secret>`."""
    if not header or not header.startswith(SIGNATURE_PREFIX) or not app_secret:
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix(SIGNATURE_PREFIX))


@dataclass(frozen=True, slots=True)
class InboundMessage:
    wa_message_id: str
    phone_e164: str
    message_type: MessageType
    # The text, the tapped button's title, or an image caption. None for other kinds.
    body: str | None
    # For button taps: the id we gave the button (e.g. "name:yes"). None otherwise.
    button_id: str | None
    # The customer's WhatsApp profile name, if Meta sent it.
    profile_name: str | None
    raw: dict[str, Any] = field(repr=False)


@dataclass(frozen=True, slots=True)
class StatusUpdate:
    wa_message_id: str
    status: MessageStatus
    error_code: str | None


@dataclass(frozen=True, slots=True)
class WebhookEvents:
    messages: list[InboundMessage]
    statuses: list[StatusUpdate]


_STATUSES = {
    "sent": MessageStatus.SENT,
    "delivered": MessageStatus.DELIVERED,
    "read": MessageStatus.READ,
    "failed": MessageStatus.FAILED,
}


def parse_webhook(payload: Any) -> WebhookEvents:
    """Everything usable in one webhook call. Anything malformed is skipped and logged, never
    raised: Meta retries a call that does not get a 200, and a bad payload never gets better."""
    messages: list[InboundMessage] = []
    statuses: list[StatusUpdate] = []
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        logger.warning("Ignoring a webhook that is not for a WhatsApp business account")
        return WebhookEvents(messages, statuses)

    for entry in _list(payload.get("entry")):
        for change in _list(_dict(entry).get("changes")):
            value = _dict(_dict(change).get("value"))
            names = {
                _dict(c).get("wa_id"): _dict(_dict(c).get("profile")).get("name")
                for c in _list(value.get("contacts"))
            }
            for raw in _list(value.get("messages")):
                message = _inbound(_dict(raw), names, value)
                if message is not None:
                    messages.append(message)
            for raw in _list(value.get("statuses")):
                update = _status(_dict(raw))
                if update is not None:
                    statuses.append(update)
    return WebhookEvents(messages, statuses)


def _inbound(
    raw: dict[str, Any], names: dict[Any, Any], value: dict[str, Any]
) -> InboundMessage | None:
    wa_id, sender = raw.get("id"), raw.get("from")
    if not isinstance(wa_id, str) or not wa_id or not isinstance(sender, str):
        logger.warning("Skipping an inbound message without an id or sender")
        return None
    phone = f"+{sender.lstrip('+')}"
    if not E164.fullmatch(phone):
        logger.warning("Skipping an inbound message from a number that is not E.164")
        return None

    kind = raw.get("type")
    body: str | None = None
    button_id: str | None = None
    if kind == "text":
        message_type, body = MessageType.TEXT, _dict(raw.get("text")).get("body")
    elif kind in ("button", "interactive"):
        message_type = MessageType.BUTTON
        body, button_id = _tapped(raw)
    elif kind == "image":
        message_type, body = MessageType.IMAGE, _dict(raw.get("image")).get("caption")
    else:  # audio, video, sticker, location, contacts, document, reaction, unsupported...
        message_type = MessageType.OTHER

    return InboundMessage(
        wa_message_id=wa_id,
        phone_e164=phone,
        message_type=message_type,
        body=body if isinstance(body, str) else None,
        button_id=button_id if isinstance(button_id, str) else None,
        profile_name=names.get(sender) if isinstance(names.get(sender), str) else None,
        raw={
            "message": raw,
            "metadata": value.get("metadata"),
            # The sender's WhatsApp profile name: the bot uses it as a new customer's name.
            "profile_name": names.get(sender) if isinstance(names.get(sender), str) else None,
        },
    )


def _tapped(raw: dict[str, Any]) -> tuple[Any, Any]:
    """(title, id) of the button a customer tapped; (None, None) if this is not a tap."""
    kind = raw.get("type")
    if kind == "button":  # quick-reply button on a template message
        button = _dict(raw.get("button"))
        return button.get("text"), button.get("payload")
    if kind == "interactive":  # reply buttons or list rows we sent
        interactive = _dict(raw.get("interactive"))
        reply = _dict(interactive.get("button_reply") or interactive.get("list_reply"))
        return reply.get("title"), reply.get("id")
    return None, None


def button_id_of(payload: Any) -> str | None:
    """The tapped button's id, read back from a stored inbound message
    (`whatsapp_messages.payload`, which keeps Meta's message as `payload["message"]`)."""
    _, button_id = _tapped(_dict(_dict(payload).get("message")))
    return button_id if isinstance(button_id, str) else None


def _status(raw: dict[str, Any]) -> StatusUpdate | None:
    wa_id, status = raw.get("id"), _STATUSES.get(raw.get("status"))  # type: ignore[arg-type]
    if not isinstance(wa_id, str) or status is None:
        return None  # e.g. "deleted" or "warning": nothing to record
    errors = _list(raw.get("errors"))
    code = _dict(errors[0]).get("code") if errors else None
    return StatusUpdate(
        wa_message_id=wa_id, status=status, error_code=str(code) if code is not None else None
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
