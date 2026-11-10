"""Handling incoming WhatsApp messages on a real database: once each, retries, the sweep."""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.whatsapp.models import WhatsAppMessage
from app.modules.whatsapp.processor import (
    FAILED_CODE,
    MAX_ATTEMPTS,
    SIMULATED_ID_PREFIX,
    InboundProcessor,
)
from tests.integration.conftest import RecordingBot

pytestmark = pytest.mark.asyncio(loop_scope="session")

SECRET = "test-app-secret"


@pytest.fixture(autouse=True)
def meta_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", SecretStr(SECRET))


def phone() -> str:
    return f"9198{uuid.uuid4().int % 10**8:08d}"


async def post_text(api: AsyncClient, sender: str, body: str, wa_id: str | None = None) -> int:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "from": sender,
                                    "id": wa_id or f"wamid.{uuid.uuid4().hex}",
                                    "type": "text",
                                    "text": {"body": body},
                                }
                            ]
                        },
                    }
                ],
            }
        ],
    }
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    response = await api.post(
        "/webhooks/whatsapp",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature},
    )
    return response.status_code


async def fresh(db: AsyncSession, **where: Any) -> list[WhatsAppMessage]:
    """Rows as the database has them now (other sessions changed them)."""
    query = (
        select(WhatsAppMessage)
        .filter_by(**where)
        .order_by(WhatsAppMessage.created_at)
        .execution_options(populate_existing=True)
    )
    return list((await db.scalars(query)).all())


async def message(
    db: AsyncSession,
    *,
    age: timedelta = timedelta(0),
    direction: str = "in",
    processed: bool = False,
    id_prefix: str = "wamid.",
) -> WhatsAppMessage:
    now = datetime.now(UTC)
    row = WhatsAppMessage(
        wa_message_id=f"{id_prefix}{uuid.uuid4().hex}",
        direction=direction,
        phone_e164=f"+{phone()}",
        message_type="text",
        body="hi",
        status="received" if direction == "in" else "sent",
        created_at=now - age,
        processed_at=now if processed else None,
    )
    db.add(row)
    await db.commit()
    return row


# --- from the webhook ---------------------------------------------------------------------------


async def test_webhook_hands_new_messages_to_the_bot(
    api: AsyncClient, db: AsyncSession, bot: RecordingBot
) -> None:
    sender = phone()

    assert await post_text(api, sender, "LOGIN") == 200

    (row,) = await fresh(db, phone_e164=f"+{sender}")
    assert bot.handled == [row.id]
    assert row.processed_at is not None
    assert row.error_code is None


async def test_a_redelivered_message_is_handled_once(
    api: AsyncClient, db: AsyncSession, bot: RecordingBot
) -> None:
    sender, wa_id = phone(), f"wamid.{uuid.uuid4().hex}"

    await post_text(api, sender, "LOGIN", wa_id)
    await post_text(api, sender, "LOGIN", wa_id)

    (row,) = await fresh(db, wa_message_id=wa_id)
    assert bot.handled == [row.id]


async def test_a_failing_bot_still_answers_meta_and_leaves_the_message_waiting(
    api: AsyncClient, db: AsyncSession, bot: RecordingBot
) -> None:
    sender = phone()
    bot.fails = True

    assert await post_text(api, sender, "LOGIN") == 200

    (row,) = await fresh(db, phone_e164=f"+{sender}")
    assert row.processed_at is None  # the sweep tries again


# --- process ------------------------------------------------------------------------------------


async def test_nothing_to_do_for_handled_or_sent_messages(
    db: AsyncSession, processor: InboundProcessor, bot: RecordingBot
) -> None:
    handled = await message(db, processed=True)
    sent = await message(db, direction="out")

    assert not await processor.process(handled.id)
    assert not await processor.process(sent.id)
    assert not await processor.process(uuid.uuid4())
    assert bot.handled == []


async def test_set_aside_after_too_many_failures(
    db: AsyncSession, processor: InboundProcessor, bot: RecordingBot, redis: FakeAsyncRedis
) -> None:
    row = await message(db)
    bot.fails = True

    for _ in range(MAX_ATTEMPTS - 1):
        assert not await processor.process(row.id)
        assert (await fresh(db, id=row.id))[0].processed_at is None
    assert not await processor.process(row.id)

    (row,) = await fresh(db, id=row.id)
    assert row.processed_at is not None
    assert row.error_code == FAILED_CODE
    assert await redis.exists(f"wa:attempts:{row.id}") == 0


async def test_success_after_a_failure_resets_the_count(
    db: AsyncSession, processor: InboundProcessor, bot: RecordingBot, redis: FakeAsyncRedis
) -> None:
    row = await message(db)
    bot.fails = True
    await processor.process(row.id)
    assert await redis.get(f"wa:attempts:{row.id}") == "1"

    bot.fails = False
    assert await processor.process(row.id)

    assert await redis.exists(f"wa:attempts:{row.id}") == 0
    assert (await fresh(db, id=row.id))[0].error_code is None


# --- sweep --------------------------------------------------------------------------------------


async def test_sweep_takes_only_messages_left_waiting_oldest_first(
    db: AsyncSession, processor: InboundProcessor, bot: RecordingBot
) -> None:
    older = await message(db, age=timedelta(minutes=10))
    old = await message(db, age=timedelta(minutes=5))
    await message(db)  # just arrived: the webhook's own call handles it
    await message(db, age=timedelta(minutes=5), processed=True)
    await message(db, age=timedelta(minutes=5), direction="out")
    await message(db, age=timedelta(minutes=5), id_prefix=SIMULATED_ID_PREFIX)  # never retried

    assert await processor.sweep() == 2

    assert bot.handled == [older.id, old.id]
    assert await processor.sweep() == 0


async def test_sweep_batch_size(
    db: AsyncSession, processor: InboundProcessor, bot: RecordingBot
) -> None:
    for minutes in (3, 2, 1):
        await message(db, age=timedelta(minutes=minutes))

    assert await processor.sweep(batch=2) == 2
    assert await processor.sweep(batch=2) == 1
