"""Scans step 8: the whole path with everything arriving at once. The webhook's parser and storage,
the real processor and the real bot, on separate connections that really commit."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.leaderboards.service import LeaderboardService, Period
from app.modules.scans import replies
from app.modules.scans.models import PointsEntry, ScanSession, Visit
from app.modules.scans.service import MAX_PEOPLE_PER_CODE
from app.modules.tags.models import Tag, VenueItem, VisitTag
from app.modules.users.consent import CONSENT_VERSION
from app.modules.users.models import User
from app.modules.venues.scan_codes import scan_text
from app.modules.whatsapp.bot import CONSENT_BUTTON, WhatsAppBot, try_row_id
from app.modules.whatsapp.dev_router import meta_payload
from app.modules.whatsapp.processor import InboundProcessor
from app.modules.whatsapp.service import WhatsAppWebhookService
from app.modules.whatsapp.webhook import parse_webhook
from tests.integration.test_scan_bot import FakeMeta, code_now
from tests.integration.test_scan_pipeline import add_venue, phone

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
def sessions(db_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
def meta() -> FakeMeta:
    return FakeMeta()


@pytest.fixture
def processor(sessions, redis, meta) -> InboundProcessor:
    return InboundProcessor(sessions, redis, WhatsAppBot(redis, meta))  # type: ignore[arg-type]


async def receive(sessions, payload: dict) -> list[uuid.UUID]:
    """What the webhook does with one delivery from Meta: store it, return what is new."""
    async with sessions() as session:
        stored = await WhatsAppWebhookService(session).store(parse_webhook(payload))
        await session.commit()
    return list(stored.new_message_ids)


async def live_venue(sessions):
    async with sessions() as session:
        venue, secret = await add_venue(session)
        await session.commit()
    return venue, secret


async def add_customer(sessions, *, agreed: bool = True) -> User:
    async with sessions() as session:
        now = datetime.now(UTC)
        user = User(
            phone_e164=phone(),
            consent_at=now if agreed else None,
            consent_version=CONSENT_VERSION if agreed else None,
        )
        session.add(user)
        await session.commit()
    return user


async def count(sessions, query) -> int:
    async with sessions() as session:
        return await session.scalar(query) or 0


def stamps(meta: FakeMeta) -> list[str]:
    return [text for text in meta.texts() if text.startswith("Stamp added")]


async def test_two_scans_at_once_make_one_visit_one_stamp(sessions, processor, meta) -> None:
    venue, secret = await live_venue(sessions)
    user = await add_customer(sessions)
    text = scan_text(venue.scan_code, code_now(secret))
    ids = [(await receive(sessions, meta_payload(user.phone_e164, text)))[0] for _ in range(2)]

    await asyncio.gather(*(processor.process(id_) for id_ in ids))

    assert len(stamps(meta)) == 1
    assert (
        meta.texts().count(f"You're already notched at {venue.name} today. Come back tomorrow!")
        == 1
    )
    visits = select(func.count()).select_from(Visit).where(Visit.user_id == user.id)
    assert await count(sessions, visits) == 1
    points = select(func.sum(PointsEntry.points)).where(PointsEntry.user_id == user.id)
    assert await count(sessions, points) == 12


async def test_webhook_redelivery_makes_one_visit_one_stamp(sessions, processor, meta) -> None:
    venue, secret = await live_venue(sessions)
    user = await add_customer(sessions)
    payload = meta_payload(user.phone_e164, scan_text(venue.scan_code, code_now(secret)))

    deliveries = await asyncio.gather(*(receive(sessions, payload) for _ in range(3)))
    new_ids = [id_ for delivery in deliveries for id_ in delivery]
    await asyncio.gather(*(processor.process(id_) for id_ in new_ids))

    assert len(new_ids) == 1  # Meta's id makes the others harmless
    assert len(stamps(meta)) == 1
    visits = select(func.count()).select_from(Visit).where(Visit.user_id == user.id)
    assert await count(sessions, visits) == 1
    scans = select(func.count()).select_from(ScanSession).where(ScanSession.user_id == user.id)
    assert await count(sessions, scans) == 1


async def test_one_message_taken_twice_at_once_replies_once(sessions, processor, meta) -> None:
    """The webhook's call and the sweep both picking up the same message."""
    venue, secret = await live_venue(sessions)
    user = await add_customer(sessions)
    text = scan_text(venue.scan_code, code_now(secret))
    (message_id,) = await receive(sessions, meta_payload(user.phone_e164, text))

    results = await asyncio.gather(*(processor.process(message_id) for _ in range(4)))

    assert results.count(True) == 1
    assert len(stamps(meta)) == 1
    visits = select(func.count()).select_from(Visit).where(Visit.user_id == user.id)
    assert await count(sessions, visits) == 1


async def test_many_customers_at_once_up_to_the_code_limit(
    sessions, processor, meta, redis
) -> None:
    venue, secret = await live_venue(sessions)
    extra = 2
    users = [await add_customer(sessions) for _ in range(MAX_PEOPLE_PER_CODE + extra)]
    code = code_now(secret)
    ids = [
        (await receive(sessions, meta_payload(u.phone_e164, scan_text(venue.scan_code, code))))[0]
        for u in users
    ]

    await asyncio.gather(*(processor.process(id_) for id_ in ids))

    assert len(stamps(meta)) == MAX_PEOPLE_PER_CODE
    notches = select(Visit.notch_no).where(Visit.venue_id == venue.id)
    async with sessions() as session:
        numbers = sorted((await session.scalars(notches)).all())
    assert numbers == list(range(1, MAX_PEOPLE_PER_CODE + 1))
    assert await redis.zcard(f"lb:{venue.city_id}:all") == MAX_PEOPLE_PER_CODE


async def test_ranks_after_a_rush_match_a_rebuild(sessions, processor, redis) -> None:
    """What the scans added one by one is exactly what the ledger says."""
    venue, secret = await live_venue(sessions)
    users = [await add_customer(sessions) for _ in range(MAX_PEOPLE_PER_CODE)]
    code = code_now(secret)
    ids = [
        (await receive(sessions, meta_payload(u.phone_e164, scan_text(venue.scan_code, code))))[0]
        for u in users
    ]
    await asyncio.gather(*(processor.process(id_) for id_ in ids))
    key = f"lb:{venue.city_id}:all"
    live = await redis.zrange(key, 0, -1, withscores=True)

    async with sessions() as session:
        await LeaderboardService(redis).rebuild(session)

    assert await redis.zrange(key, 0, -1, withscores=True) == live
    rank = await LeaderboardService(redis).rank_of(venue.city_id, users[0].id, Period.ALL)
    assert (rank.rank, rank.points) == (1, 12)  # all equal, so all share first place


async def test_two_first_messages_from_a_new_number_make_one_customer(
    sessions, processor, meta
) -> None:
    number = phone()
    ids = [(await receive(sessions, meta_payload(number, "hi")))[0] for _ in range(2)]

    await asyncio.gather(*(processor.process(id_) for id_ in ids))

    customers = select(func.count()).select_from(User).where(User.phone_e164 == number)
    assert await count(sessions, customers) == 1


async def test_a_new_number_scanning_twice_at_once(sessions, processor, meta) -> None:
    venue, secret = await live_venue(sessions)
    number = phone()
    text = scan_text(venue.scan_code, code_now(secret))
    ids = [(await receive(sessions, meta_payload(number, text)))[0] for _ in range(2)]

    await asyncio.gather(*(processor.process(id_) for id_ in ids))

    async with sessions() as session:
        user = await session.scalar(select(User).where(User.phone_e164 == number))
        visits = await session.scalar(
            select(func.count()).select_from(Visit).where(Visit.user_id == user.id)
        )
    assert visits == 1
    assert len(stamps(meta)) == 1


async def test_two_consent_taps_at_once_reply_once(sessions, processor, meta) -> None:
    user = await add_customer(sessions, agreed=False)
    ids = [
        (await receive(sessions, meta_payload(user.phone_e164, None, CONSENT_BUTTON.id)))[0]
        for _ in range(2)
    ]

    await asyncio.gather(*(processor.process(id_) for id_ in ids))

    assert len(meta.sent) == 1
    async with sessions() as session:
        saved = await session.get(User, user.id)
    assert saved.consent_at is not None


async def test_two_menu_taps_at_once_save_once_and_reply_once(sessions, processor, meta) -> None:
    venue, secret = await live_venue(sessions)
    async with sessions() as session:
        name = f"Mocha {uuid.uuid4().hex[:6]}"
        tag = Tag(name=name, slug=name.lower().replace(" ", "-"), kind="item")
        session.add(tag)
        await session.flush()
        session.add(VenueItem(venue_id=venue.id, tag_id=tag.id, position=0))
        await session.commit()
    user = await add_customer(sessions)
    text = scan_text(venue.scan_code, code_now(secret))
    (scan_id,) = await receive(sessions, meta_payload(user.phone_e164, text))
    await processor.process(scan_id)
    async with sessions() as session:
        visit_id = await session.scalar(select(Visit.id).where(Visit.user_id == user.id))
    meta.sent.clear()
    row = try_row_id(visit_id, tag.id)
    ids = [(await receive(sessions, meta_payload(user.phone_e164, None, row)))[0] for _ in range(2)]

    await asyncio.gather(*(processor.process(id_) for id_ in ids))

    assert meta.texts() == [replies.tried_text(tag.name)]
    answers = select(func.count()).select_from(VisitTag).where(VisitTag.visit_id == visit_id)
    assert await count(sessions, answers) == 1


async def test_a_rush_at_many_places_never_mixes_them_up(sessions, processor, meta) -> None:
    """Different people scanning at different places in the same instant: every Notch lands at
    the place whose code was sent, and each place's Notch numbers run 1, 2, 3 with no gaps."""
    places = [await live_venue(sessions) for _ in range(6)]
    per_place = MAX_PEOPLE_PER_CODE
    scans: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]] = []  # (message, customer, venue)
    for venue, secret in places:
        for _ in range(per_place):
            user = await add_customer(sessions)
            text = scan_text(venue.scan_code, code_now(secret))
            [message_id] = await receive(sessions, meta_payload(user.phone_e164, text))
            scans.append((message_id, user.id, venue.id))

    # Interleaved, so no place's scans arrive together.
    order = sorted(scans, key=lambda scan: scan[0].int % 97)
    await asyncio.gather(*(processor.process(message_id) for message_id, _, _ in order))

    async with sessions() as session:
        for _, user_id, venue_id in scans:
            visits = (await session.scalars(select(Visit).where(Visit.user_id == user_id))).all()
            assert [v.venue_id for v in visits] == [venue_id]  # one Notch, at their own place
            scan = await session.scalar(select(ScanSession).where(ScanSession.user_id == user_id))
            assert scan.venue_id == venue_id
        for venue, _ in places:
            numbers = await session.scalars(
                select(Visit.notch_no).where(Visit.venue_id == venue.id).order_by(Visit.notch_no)
            )
            assert numbers.all() == list(range(1, per_place + 1))
    assert len(stamps(meta)) == len(places) * per_place
