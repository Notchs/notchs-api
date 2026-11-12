"""Storing what Meta's webhook delivers: inbound messages and status updates for our sends."""

import uuid
from dataclasses import dataclass

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ids import new_uuid
from app.modules.users.models import User
from app.modules.whatsapp.models import (
    MessageDirection,
    MessageStatus,
    WhatsAppMessage,
)
from app.modules.whatsapp.webhook import InboundMessage, StatusUpdate, WebhookEvents

MAX_ERROR_CODE_LENGTH = 20
MAX_ERROR_TITLE_LENGTH = 255

# Statuses only move forward: a late "delivered" never overwrites "read".
_RANK = {
    MessageStatus.SENT: 1,
    MessageStatus.DELIVERED: 2,
    MessageStatus.READ: 3,
    MessageStatus.FAILED: 4,
}


@dataclass(frozen=True, slots=True)
class StoredEvents:
    # Ids of inbound messages stored for the first time (duplicates from Meta excluded).
    new_message_ids: list[uuid.UUID]
    statuses_applied: int


class WhatsAppWebhookService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def store(self, events: WebhookEvents) -> StoredEvents:
        new_ids = [
            stored
            for message in events.messages
            if (stored := await self._store_inbound(message)) is not None
        ]
        applied = sum([await self._apply_status(s) for s in events.statuses])
        return StoredEvents(new_message_ids=new_ids, statuses_applied=applied)

    async def _store_inbound(self, message: InboundMessage) -> uuid.UUID | None:
        """Insert once. A message Meta delivers again hits the unique id and is skipped."""
        user_id = await self.session.scalar(
            select(User.id).where(User.phone_e164 == message.phone_e164)
        )
        return await self.session.scalar(
            insert(WhatsAppMessage)
            .values(
                id=new_uuid(),
                wa_message_id=message.wa_message_id,
                direction=MessageDirection.IN,
                phone_e164=message.phone_e164,
                user_id=user_id,
                message_type=message.message_type,
                body=message.body,
                payload=message.raw,
                status=MessageStatus.RECEIVED,
            )
            .on_conflict_do_nothing(index_elements=[WhatsAppMessage.wa_message_id])
            .returning(WhatsAppMessage.id)
        )

    async def _apply_status(self, status: StatusUpdate) -> int:
        """Update one of OUR sent messages. Unknown ids (sent from Meta's dashboard, say) and
        backwards moves are ignored."""
        current_rank = case(
            *[(WhatsAppMessage.status == s.value, r) for s, r in _RANK.items()], else_=0
        )
        values: dict[str, object] = {"status": status.status}
        if status.error_code is not None:
            values["error_code"] = status.error_code[:MAX_ERROR_CODE_LENGTH]
        if status.error_title is not None:
            values["error_title"] = status.error_title[:MAX_ERROR_TITLE_LENGTH]
        # When it arrived and was read. "read" implies delivered, if that came late or never.
        if status.status in (MessageStatus.DELIVERED, MessageStatus.READ):
            values["delivered_at"] = func.coalesce(WhatsAppMessage.delivered_at, func.now())
        if status.status == MessageStatus.READ:
            values["read_at"] = func.coalesce(WhatsAppMessage.read_at, func.now())
        result = await self.session.execute(
            update(WhatsAppMessage)
            .where(
                WhatsAppMessage.wa_message_id == status.wa_message_id,
                WhatsAppMessage.direction == MessageDirection.OUT,
                current_rank < _RANK[status.status],
            )
            .values(**values)
        )
        return result.rowcount or 0
