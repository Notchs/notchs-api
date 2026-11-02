"""Doubled actions, retries, races and provider failures around check-ins, consent and
deletion. One action must never make two Notchs, two deletions or two "Done" messages, and a
failure must leave a state that can be retried."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.whatsapp import SendResult, WhatsAppSendError
from app.modules.jobs.runner import SessionFactory
from app.modules.notches.checkin import CheckInService, Outcome, delete_expired_pending
from app.modules.notches.codes import ScanMessage, message_template
from app.modules.notches.models import Notch, PendingCheckIn
from app.modules.users.consent import PRIVACY_CONSENT_TEXT
from app.modules.users.deletion import DeletionService
from app.modules.users.models import (
    PRIVACY_NOTICE_VERSION,
    DeletionStatus,
    User,
    UserConsent,
    UserDeletion,
    UserStatus,
)
from app.modules.venues.models import Venue
from app.modules.whatsapp.bot import WhatsAppBot
from app.modules.whatsapp.dev_router import CapturingClient, meta_payload
from app.modules.whatsapp.models import MessageStatus, WhatsAppMessage
from app.modules.whatsapp.processor import InboundProcessor
from app.modules.whatsapp.service import WhatsAppWebhookService
from app.modules.whatsapp.webhook import parse_webhook
from tests import factories as f

PHONE = "+919811111111"


async def _store(db: AsyncSession, text: str | None = None, button: str | None = None):
    stored = await WhatsAppWebhookService(db).store(
        parse_webhook(meta_payload(PHONE, text, button))
    )
    return stored.new_message_ids[0]


async def _say(
    db: AsyncSession,
    redis: FakeAsyncRedis,
    text: str | None = None,
    button: str | None = None,
    client: Any = None,
) -> list[str]:
    message = await db.get(WhatsAppMessage, await _store(db, text, button))
    client = client or CapturingClient()
    await WhatsAppBot(redis, client)(db, message)
    await db.flush()
    return client.texts() if isinstance(client, CapturingClient) else []


async def _live_venue(db: AsyncSession):
    city = await f.city(db)
    venue = await f.venue(db, city, name="Kahwa House")
    await f.counter(db, venue)
    return venue


def _scan(venue: Venue) -> str:
    return message_template(venue.name, venue.venue_code).replace(
        "{code}", f.code_at(datetime.now(UTC))
    )


async def _notches(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(Notch)) or 0


async def test_two_i_agree_taps_make_one_notch(db: AsyncSession, redis: FakeAsyncRedis) -> None:
    venue = await _live_venue(db)
    await _say(db, redis, _scan(venue))

    first = await _say(db, redis, button="consent_yes")
    second = await _say(db, redis, button="consent_yes")

    assert first[0].startswith("*Notched.*")
    assert second == ["*You're in.* Scan the screen at any Notchs counter to get your first Notch."]
    assert await _notches(db) == 1


async def test_consent_is_stored_with_its_exact_wording(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    venue = await _live_venue(db)
    await _say(db, redis, _scan(venue))
    await _say(db, redis, "i agree")

    consent = await db.scalar(select(UserConsent))
    assert consent.wording.startswith(PRIVACY_CONSENT_TEXT)
    assert consent.wording.endswith("(typed I AGREE)")
    assert consent.version == PRIVACY_NOTICE_VERSION


async def test_a_failed_consent_leaves_the_scan_waiting(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    venue = await _live_venue(db)
    await _say(db, redis, _scan(venue))
    user = await db.scalar(select(User).where(User.phone_e164 == PHONE))
    user.consent_at = datetime.now(UTC)
    user_id = user.id

    # The transaction that uses the waiting scan fails after making the Notch...
    with pytest.raises(RuntimeError):
        async with db.begin_nested():
            result = await CheckInService(db, redis).complete_pending(user)
            assert result.outcome is Outcome.NOTCHED
            raise RuntimeError("database went away")
    # ...so nothing is kept, the scan still waits, and the retry makes exactly one Notch.
    assert await _notches(db) == 0
    assert await db.get(PendingCheckIn, user_id) is not None
    await db.refresh(user)  # the rollback expired what it had loaded
    retry = await CheckInService(db, redis).complete_pending(user)
    assert retry.outcome is Outcome.NOTCHED
    assert await _notches(db) == 1


async def test_processor_retry_after_a_failure_notches_once(
    db: AsyncSession, redis: FakeAsyncRedis, session_factory: SessionFactory
) -> None:
    venue = await _live_venue(db)
    await f.user(db, phone_e164=PHONE)
    message_id = await _store(db, _scan(venue))
    await db.flush()
    capture = CapturingClient()
    bot = WhatsAppBot(redis, capture)  # type: ignore[arg-type]
    calls = 0

    async def flaky(session: AsyncSession, message: WhatsAppMessage) -> None:
        nonlocal calls
        calls += 1
        await bot(session, message)
        if calls == 1:
            raise RuntimeError("crash after the Notch, before the commit")

    processor = InboundProcessor(session_factory, redis, flaky)
    assert await processor.process(message_id) is False  # rolled back
    assert await _notches(db) == 0
    assert await processor.process(message_id) is True  # the retry
    assert await processor.process(message_id) is False  # already handled: nothing twice
    assert await _notches(db) == 1


async def test_a_race_on_the_one_per_day_index_is_already_today(
    db: AsyncSession, redis: FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    venue = await _live_venue(db)
    user = await f.user(db)
    service = CheckInService(db, redis)
    now = datetime.now(UTC)
    await f.notch_at(db, redis, user, venue, now)

    # Two messages handled at the same moment both pass the "already today?" check.
    async def not_yet(*_: Any) -> bool:
        return False

    monkeypatch.setattr(service, "_already_today", not_yet)
    later = now + timedelta(seconds=5)
    raced = await service.scan(
        user, ScanMessage(venue.venue_code, f.code_at(later)), scanned_at=later
    )

    assert raced.outcome is Outcome.ALREADY_TODAY
    assert await _notches(db) == 1
    await db.refresh(venue)
    assert venue.notch_count == 1  # the losing attempt's number was rolled back too


async def test_whatsapp_down_still_keeps_the_notch_and_never_retries_it(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    venue = await _live_venue(db)
    await f.user(db, phone_e164=PHONE)

    class Down:
        async def send(self, payload: dict[str, Any]) -> SendResult:
            raise WhatsAppSendError("131000", "Something went wrong")

    await _say(db, redis, _scan(venue), client=Down())

    assert await _notches(db) == 1
    failed = (
        await db.scalars(
            select(WhatsAppMessage).where(WhatsAppMessage.status == MessageStatus.FAILED)
        )
    ).all()
    assert len(failed) >= 1 and failed[0].error_code == "131000"


async def test_expired_waiting_scans_are_cleaned_up(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    venue = await _live_venue(db)
    await _say(db, redis, _scan(venue))
    await db.execute(
        update(PendingCheckIn).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )

    assert await delete_expired_pending(db) == 1
    user = await db.scalar(select(User).where(User.phone_e164 == PHONE))
    assert await CheckInService(db, redis).complete_pending(user) is None


async def test_yes_twice_deletes_once_and_says_done_once(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    await f.user(db, phone_e164=PHONE)
    await _say(db, redis, "DELETE")

    assert await _say(db, redis, "YES") == ["*Done.* Everything is deleted."]
    assert await _say(db, redis, "YES") == []  # the account is closed: no reply at all
    rows = (await db.scalars(select(UserDeletion))).all()
    assert [r.status for r in rows] == [DeletionStatus.CONFIRMED]


async def test_yes_after_the_window_does_not_delete(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    user = await f.user(db, phone_e164=PHONE)
    deletion = await DeletionService(db).ask(user)
    deletion.created_at = datetime.now(UTC) - timedelta(minutes=11)
    await db.flush()

    assert await DeletionService(db).confirm(user) is False
    assert user.status == UserStatus.ACTIVE


async def test_two_delete_requests_then_yes_settles_both(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    user = await f.user(db, phone_e164=PHONE)
    await _say(db, redis, "DELETE")
    await _say(db, redis, "DELETE")
    await _say(db, redis, "YES")

    statuses = sorted(r.status for r in (await db.scalars(select(UserDeletion))).all())
    assert statuses == [DeletionStatus.CONFIRMED, DeletionStatus.EXPIRED]
    await db.refresh(user)
    assert user.status == UserStatus.DELETED
