"""Sending a WhatsApp message and recording it in `whatsapp_messages`.

Every message we send is stored, whether Meta accepted it, it was a dry run, or it failed.
A failed send does not raise: the bot keeps going, and the stored row (status `failed`,
Meta's `error_code`) shows what happened.
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.whatsapp import WhatsAppClient, WhatsAppSendError
from app.common.phone import masked_phone
from app.core.logging import get_logger
from app.modules.whatsapp import payloads
from app.modules.whatsapp.models import (
    MessageDirection,
    MessageStatus,
    MessageType,
    WhatsAppMessage,
)
from app.modules.whatsapp.payloads import ReplyButton

logger = get_logger(__name__)

MAX_ERROR_CODE_LENGTH = 20  # whatsapp_messages.error_code


def local_message_id() -> str:
    """For outbound messages Meta never accepted (dry run, failures): still unique."""
    return f"local:{uuid.uuid4()}"


class WhatsAppSender:
    def __init__(self, session: AsyncSession, client: WhatsAppClient) -> None:
        self.session = session
        self.client = client

    async def send_text(
        self, phone_e164: str, body: str, *, user_id: uuid.UUID | None = None
    ) -> WhatsAppMessage:
        payload = payloads.text_message(phone_e164, body)
        return await self._send(phone_e164, payload, MessageType.TEXT, body, user_id)

    async def send_buttons(
        self,
        phone_e164: str,
        body: str,
        buttons: list[ReplyButton],
        *,
        user_id: uuid.UUID | None = None,
    ) -> WhatsAppMessage:
        payload = payloads.buttons_message(phone_e164, body, buttons)
        return await self._send(phone_e164, payload, MessageType.BUTTON, body, user_id)

    async def _send(
        self,
        phone_e164: str,
        payload: dict[str, Any],
        message_type: MessageType,
        body: str,
        user_id: uuid.UUID | None,
    ) -> WhatsAppMessage:
        message = WhatsAppMessage(
            direction=MessageDirection.OUT,
            phone_e164=phone_e164,
            user_id=user_id,
            message_type=message_type,
            body=body,
            payload=payload,
        )
        try:
            result = await self.client.send(payload)
        except WhatsAppSendError as exc:
            logger.error("WhatsApp message to %s failed: %s", masked_phone(phone_e164), exc)
            message.wa_message_id = local_message_id()
            message.status = MessageStatus.FAILED
            message.error_code = exc.code[:MAX_ERROR_CODE_LENGTH]
        else:
            message.wa_message_id = result.wa_message_id or local_message_id()
            message.status = MessageStatus.SENT
        self.session.add(message)
        await self.session.flush()
        return message
