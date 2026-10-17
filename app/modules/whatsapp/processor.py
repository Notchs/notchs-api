"""Handling incoming WhatsApp messages, each exactly once, inside the API process.

    webhook   stores new messages, commits, then calls process_many(ids) after answering Meta
    sweep     every minute (and at startup): anything inbound still unhandled after a short
              grace period, e.g. because the server restarted mid-way (simulated ones excepted)
    process   locks the row (FOR UPDATE SKIP LOCKED, so two processes never take the same
              message), runs the bot, sets processed_at

A message whose handling keeps failing is retried up to MAX_ATTEMPTS times, then set aside
(processed_at set, error_code "processing_failed") and logged, so it cannot block anything.

Everything here can move to a separate worker later without changes: it only needs a session
factory, Redis and the handler.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.whatsapp import get_whatsapp_client
from app.core.logging import get_logger
from app.core.redis import redis_client
from app.db.session import AsyncSessionLocal
from app.modules.whatsapp.bot import WhatsAppBot
from app.modules.whatsapp.models import MessageDirection, WhatsAppMessage

logger = get_logger(__name__)

Handler = Callable[[AsyncSession, WhatsAppMessage], Awaitable[None]]
SessionFactory = Callable[[], AsyncSession]

MAX_ATTEMPTS = 3
FAILED_CODE = "processing_failed"
ATTEMPTS_TTL_SECONDS = 24 * 60 * 60
# The sweep leaves fresh messages to the webhook's own call, so they are not handled twice
# in a race (the row lock would stop that anyway; this just avoids the wasted work).
SWEEP_GRACE = timedelta(seconds=30)
SWEEP_BATCH = 50
# Messages made by /dev/simulate-inbound. The sweep never retries them: it would use the real
# WhatsApp client and could message a real number.
SIMULATED_ID_PREFIX = "sim:"


def _attempts_key(message_id: uuid.UUID) -> str:
    return f"wa:attempts:{message_id}"


class InboundProcessor:
    def __init__(
        self,
        session_factory: SessionFactory,
        redis: Redis,
        handler: Handler,
    ) -> None:
        self.session_factory = session_factory
        self.redis = redis
        self.handler = handler

    async def process(self, message_id: uuid.UUID) -> bool:
        """Handle one message. Returns True if this call handled it, False if there was nothing
        to do (already handled, being handled elsewhere, not inbound) or it failed."""
        async with self.session_factory() as session:
            message = await session.scalar(
                select(WhatsAppMessage)
                .where(
                    WhatsAppMessage.id == message_id,
                    WhatsAppMessage.direction == MessageDirection.IN,
                    WhatsAppMessage.processed_at.is_(None),
                )
                .with_for_update(skip_locked=True)
            )
            if message is None:
                return False
            try:
                await self.handler(session, message)
                message.processed_at = datetime.now(UTC)
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("Handling WhatsApp message %s failed", message_id)
                await self._count_failure(message_id)
                return False
        await self.redis.delete(_attempts_key(message_id))
        return True

    async def process_many(self, message_ids: Iterable[uuid.UUID]) -> int:
        """In order. One failure never stops the rest."""
        handled = 0
        for message_id in message_ids:
            try:
                handled += await self.process(message_id)
            except Exception:  # e.g. the database or Redis is briefly unreachable
                logger.exception(
                    "Could not process WhatsApp message %s; the sweep retries it", message_id
                )
        return handled

    async def sweep(self, *, batch: int = SWEEP_BATCH) -> int:
        """Handle inbound messages still waiting after the grace period, oldest first."""
        async with self.session_factory() as session:
            ids = (
                await session.scalars(
                    select(WhatsAppMessage.id)
                    .where(
                        WhatsAppMessage.direction == MessageDirection.IN,
                        WhatsAppMessage.processed_at.is_(None),
                        WhatsAppMessage.created_at < datetime.now(UTC) - SWEEP_GRACE,
                        WhatsAppMessage.wa_message_id.not_like(f"{SIMULATED_ID_PREFIX}%"),
                    )
                    .order_by(WhatsAppMessage.created_at)
                    .limit(batch)
                )
            ).all()
        if ids:
            logger.info("WhatsApp sweep: %d message(s) still waiting", len(ids))
        return await self.process_many(ids)

    async def _count_failure(self, message_id: uuid.UUID) -> None:
        key = _attempts_key(message_id)
        attempts = await self.redis.incr(key)
        await self.redis.expire(key, ATTEMPTS_TTL_SECONDS)
        if attempts < MAX_ATTEMPTS:
            return
        # Give up: mark it so the sweep stops picking it, and keep why.
        async with self.session_factory() as session:
            await session.execute(
                update(WhatsAppMessage)
                .where(WhatsAppMessage.id == message_id, WhatsAppMessage.processed_at.is_(None))
                .values(processed_at=datetime.now(UTC), error_code=FAILED_CODE)
            )
            await session.commit()
        await self.redis.delete(key)
        logger.error(
            "Gave up on WhatsApp message %s after %d attempts (error_code=%s)",
            message_id,
            MAX_ATTEMPTS,
            FAILED_CODE,
        )


async def run_sweeper(processor: InboundProcessor, interval_seconds: float) -> None:
    """Background loop started with the app: sweep now, then every `interval_seconds`."""
    while True:
        try:
            await processor.sweep()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("WhatsApp sweep failed; trying again next round")
        await asyncio.sleep(interval_seconds)


def get_inbound_processor() -> InboundProcessor:
    """FastAPI dependency (and the app's sweeper): the real database, Redis and bot."""
    return InboundProcessor(
        AsyncSessionLocal, redis_client, WhatsAppBot(redis_client, get_whatsapp_client())
    )
