"""WhatsAppSender against a fake session and a fake client (no database, no Meta)."""

import uuid
from typing import Any

import pytest

from app.clients.whatsapp import SendResult, WhatsAppSendError
from app.modules.whatsapp.models import WhatsAppMessage
from app.modules.whatsapp.payloads import ReplyButton
from app.modules.whatsapp.sender import WhatsAppSender

PHONE = "+919876543210"


class FakeSession:
    def __init__(self) -> None:
        self.added: list[WhatsAppMessage] = []

    def add(self, obj: WhatsAppMessage) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass


class FakeClient:
    def __init__(
        self, result: SendResult | None = None, error: WhatsAppSendError | None = None
    ) -> None:
        self.result, self.error = result, error
        self.sent: list[dict[str, Any]] = []

    async def send(self, payload: dict[str, Any]) -> SendResult:
        self.sent.append(payload)
        if self.error:
            raise self.error
        assert self.result is not None
        return self.result


def _sender(client: FakeClient) -> tuple[WhatsAppSender, FakeSession]:
    session = FakeSession()
    return WhatsAppSender(session, client), session  # type: ignore[arg-type]


async def test_sent_message_is_stored_with_metas_id() -> None:
    user_id = uuid.uuid4()
    sender, session = _sender(FakeClient(SendResult("wamid.ABC", dry_run=False)))

    message = await sender.send_text(PHONE, "Stamp added!", user_id=user_id)

    assert session.added == [message]
    assert (message.wa_message_id, message.direction, message.status) == (
        "wamid.ABC",
        "out",
        "sent",
    )
    assert (message.message_type, message.body, message.user_id) == (
        "text",
        "Stamp added!",
        user_id,
    )
    assert message.payload["to"] == "919876543210"


async def test_dry_run_is_stored_with_a_local_id() -> None:
    sender, _ = _sender(FakeClient(SendResult(None, dry_run=True)))

    message = await sender.send_text(PHONE, "hi")

    assert message.wa_message_id.startswith("local:")
    assert message.status == "sent"


async def test_failure_is_stored_and_does_not_raise() -> None:
    sender, session = _sender(
        FakeClient(error=WhatsAppSendError("131047", "Re-engagement message"))
    )

    message = await sender.send_text(PHONE, "hi")

    assert session.added == [message]
    assert (message.status, message.error_code) == ("failed", "131047")
    assert message.wa_message_id.startswith("local:")


async def test_long_error_codes_fit_the_column() -> None:
    sender, _ = _sender(FakeClient(error=WhatsAppSendError("x" * 50, "odd")))

    assert len((await sender.send_text(PHONE, "hi")).error_code or "") == 20


async def test_buttons_are_stored_as_button_messages() -> None:
    client = FakeClient(SendResult("wamid.B", dry_run=False))
    sender, _ = _sender(client)

    message = await sender.send_buttons(
        PHONE, "Pick", [ReplyButton("a", "Yes"), ReplyButton("b", "No")]
    )

    assert message.message_type == "button"
    assert client.sent[0]["type"] == "interactive"


async def test_invalid_messages_are_refused_before_sending() -> None:
    client = FakeClient(SendResult("wamid.X", dry_run=False))
    sender, session = _sender(client)

    with pytest.raises(ValueError):
        await sender.send_text("98765", "hi")

    assert client.sent == [] and session.added == []
