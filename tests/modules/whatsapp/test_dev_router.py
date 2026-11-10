"""The simulator's fake Meta pieces (no database)."""

from app.modules.whatsapp import payloads
from app.modules.whatsapp.dev_router import CapturingClient, meta_payload
from app.modules.whatsapp.models import MessageType
from app.modules.whatsapp.payloads import ReplyButton
from app.modules.whatsapp.webhook import parse_webhook


def test_simulated_payload_reads_like_metas() -> None:
    (message,) = parse_webhook(meta_payload("+919876543210", "LOGIN")).messages

    assert (message.phone_e164, message.message_type, message.body) == (
        "+919876543210",
        MessageType.TEXT,
        "LOGIN",
    )
    assert message.wa_message_id.startswith("sim:")


def test_simulated_ids_are_unique() -> None:
    first, second = (
        parse_webhook(meta_payload("+919876543210", "hi")).messages[0].wa_message_id
        for _ in range(2)
    )

    assert first != second


async def test_capturing_client_keeps_texts_and_sends_nothing() -> None:
    client = CapturingClient()

    result = await client.send(payloads.text_message("+919876543210", "one"))
    await client.send(
        payloads.buttons_message("+919876543210", "two", [ReplyButton(id="a", title="A")])
    )

    assert (result.wa_message_id, result.dry_run) == (None, True)
    assert client.texts() == ["one", "two\n[A]"]  # buttons shown under the text


def test_simulated_button_tap_reads_like_metas() -> None:
    (message,) = parse_webhook(meta_payload("+919876543210", None, "login")).messages

    assert (message.message_type, message.button_id) == (MessageType.BUTTON, "login")
