"""Meta's webhook payloads, turned into records (pure functions, no database)."""

import hashlib
import hmac
from typing import Any

import pytest

from app.modules.whatsapp.models import MessageStatus, MessageType
from app.modules.whatsapp.webhook import button_id_of, parse_webhook, signature_is_valid

SECRET = "app-secret"


def envelope(value: dict[str, Any]) -> dict[str, Any]:
    """Meta's wrapper around every change."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550001111",
                                "phone_number_id": "123",
                            },
                            **value,
                        },
                    }
                ],
            }
        ],
    }


def inbound(message: dict[str, Any], name: str | None = "Riya Mehta") -> dict[str, Any]:
    contacts = [{"wa_id": "919876543210", "profile": {"name": name}}] if name else []
    return envelope(
        {
            "contacts": contacts,
            "messages": [
                {"from": "919876543210", "id": "wamid.IN1", "timestamp": "1760000000", **message}
            ],
        }
    )


# --- signature ------------------------------------------------------------------------------


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature() -> None:
    assert signature_is_valid(b'{"a":1}', sign(b'{"a":1}'), SECRET)


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "sha256=",
        "md5=abc",
        sign(b'{"a":2}'),
        sign(b'{"a":1}', "other-secret"),
        sign(b'{"a":1}').upper(),
    ],
    ids=[
        "missing",
        "empty",
        "no-hash",
        "wrong-algorithm",
        "other-body",
        "other-secret",
        "uppercase",
    ],
)
def test_invalid_signatures(header: str | None) -> None:
    assert not signature_is_valid(b'{"a":1}', header, SECRET)


def test_no_secret_never_validates() -> None:
    assert not signature_is_valid(b"{}", sign(b"{}", ""), "")


# --- inbound messages ---------------------------------------------------------------------------


def test_text_message() -> None:
    (message,) = parse_webhook(
        inbound({"type": "text", "text": {"body": "NOTCH KAHWA 482913"}})
    ).messages

    assert message.wa_message_id == "wamid.IN1"
    assert message.phone_e164 == "+919876543210"
    assert (message.message_type, message.body, message.button_id) == (
        MessageType.TEXT,
        "NOTCH KAHWA 482913",
        None,
    )
    assert message.profile_name == "Riya Mehta"
    assert message.raw["message"]["text"]["body"] == "NOTCH KAHWA 482913"
    assert message.raw["metadata"]["phone_number_id"] == "123"


def test_reply_button_tap() -> None:
    (message,) = parse_webhook(
        inbound(
            {
                "type": "interactive",
                "interactive": {
                    "type": "button_reply",
                    "button_reply": {"id": "name:yes", "title": "Use Riya"},
                },
            }
        )
    ).messages

    assert (message.message_type, message.body, message.button_id) == (
        MessageType.BUTTON,
        "Use Riya",
        "name:yes",
    )


def test_list_reply() -> None:
    (message,) = parse_webhook(
        inbound(
            {
                "type": "interactive",
                "interactive": {
                    "type": "list_reply",
                    "list_reply": {"id": "tag:matcha", "title": "Matcha"},
                },
            }
        )
    ).messages

    assert (message.message_type, message.body, message.button_id) == (
        MessageType.BUTTON,
        "Matcha",
        "tag:matcha",
    )


def test_template_quick_reply_button() -> None:
    (message,) = parse_webhook(
        inbound({"type": "button", "button": {"text": "Stop promotions", "payload": "stop"}})
    ).messages

    assert (message.message_type, message.body, message.button_id) == (
        MessageType.BUTTON,
        "Stop promotions",
        "stop",
    )


def test_image_with_caption() -> None:
    (message,) = parse_webhook(
        inbound({"type": "image", "image": {"id": "MEDIA1", "caption": "my bill"}})
    ).messages

    assert (message.message_type, message.body) == (MessageType.IMAGE, "my bill")


@pytest.mark.parametrize(
    "kind",
    ["audio", "sticker", "location", "reaction", "video", "document", "contacts", "unsupported"],
)
def test_other_kinds_are_kept_as_other(kind: str) -> None:
    (message,) = parse_webhook(inbound({"type": kind, kind: {"id": "X"}})).messages

    assert (message.message_type, message.body) == (MessageType.OTHER, None)
    assert message.raw["message"]["type"] == kind


def test_missing_profile_name() -> None:
    (message,) = parse_webhook(
        inbound({"type": "text", "text": {"body": "hi"}}, name=None)
    ).messages

    assert message.profile_name is None


def test_several_messages_in_one_call() -> None:
    payload = envelope(
        {
            "messages": [
                {"from": "919876543210", "id": "wamid.A", "type": "text", "text": {"body": "one"}},
                {"from": "447700900123", "id": "wamid.B", "type": "text", "text": {"body": "two"}},
            ]
        }
    )

    messages = parse_webhook(payload).messages

    assert [(m.wa_message_id, m.phone_e164) for m in messages] == [
        ("wamid.A", "+919876543210"),
        ("wamid.B", "+447700900123"),
    ]


@pytest.mark.parametrize(
    "message",
    [
        {"from": "919876543210", "type": "text", "text": {"body": "no id"}},
        {"id": "wamid.X", "type": "text", "text": {"body": "no sender"}},
        {"from": "12", "id": "wamid.X", "type": "text", "text": {"body": "bad number"}},
        "not even a dict",
    ],
    ids=["no-id", "no-sender", "bad-number", "not-a-dict"],
)
def test_broken_messages_are_skipped(message: Any) -> None:
    assert parse_webhook(envelope({"messages": [message]})).messages == []


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            {"type": "interactive", "interactive": {"button_reply": {"id": "login", "title": "x"}}},
            "login",
        ),
        ({"type": "interactive", "interactive": {"list_reply": {"id": "tag:a"}}}, "tag:a"),
        ({"type": "button", "button": {"text": "Stop", "payload": "stop"}}, "stop"),
        ({"type": "text", "text": {"body": "login"}}, None),
        ({"type": "interactive", "interactive": {"button_reply": {"id": 5}}}, None),
    ],
    ids=["reply-button", "list-row", "template-button", "text", "id-not-a-string"],
)
def test_button_id_from_a_stored_payload(message: dict[str, Any], expected: str | None) -> None:
    assert button_id_of({"message": message, "metadata": {}}) == expected


@pytest.mark.parametrize("payload", [None, {}, {"message": "x"}, []])
def test_button_id_from_nothing_usable(payload: Any) -> None:
    assert button_id_of(payload) is None


# --- statuses ---------------------------------------------------------------------------------


def statuses(*items: dict[str, Any]) -> dict[str, Any]:
    return envelope({"statuses": list(items)})


def test_status_updates() -> None:
    events = parse_webhook(
        statuses(
            {"id": "wamid.OUT1", "status": "delivered", "recipient_id": "919876543210"},
            {"id": "wamid.OUT2", "status": "read"},
            {
                "id": "wamid.OUT3",
                "status": "failed",
                "errors": [{"code": 131047, "title": "Re-engagement message"}],
            },
        )
    )

    assert [(s.wa_message_id, s.status, s.error_code) for s in events.statuses] == [
        ("wamid.OUT1", MessageStatus.DELIVERED, None),
        ("wamid.OUT2", MessageStatus.READ, None),
        ("wamid.OUT3", MessageStatus.FAILED, "131047"),
    ]
    assert events.messages == []


def test_unknown_statuses_are_ignored() -> None:
    assert (
        parse_webhook(statuses({"id": "wamid.X", "status": "deleted"}, {"status": "read"})).statuses
        == []
    )


# --- not for us ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "text",
        {"object": "page", "entry": []},
        {"object": "whatsapp_business_account"},
        {"object": "whatsapp_business_account", "entry": "x"},
    ],
)
def test_payloads_without_anything_usable(payload: Any) -> None:
    events = parse_webhook(payload)

    assert (events.messages, events.statuses) == ([], [])
