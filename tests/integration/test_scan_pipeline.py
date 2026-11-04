"""Scans step 4 on a real database: every outcome of the pipeline, and scans arriving at once."""

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.common import crypto
from app.modules.locations.models import City
from app.modules.scans import verifier
from app.modules.scans.models import (
    PointsEntry,
    RejectReason,
    ScanSession,
    ScanStatus,
    Visit,
    VisitStatus,
)
from app.modules.scans.parser import Scan
from app.modules.scans.service import (
    MAX_PEOPLE_PER_CODE,
    MAX_WRONG_CODES,
    ScanResult,
    ScanService,
)
from app.modules.users.models import User, UserStatus
from app.modules.venues.counters import new_totp_secret
from app.modules.venues.models import CounterDevice, Venue, VenueStatus
from app.modules.whatsapp.bot import find_or_create_customer
from app.modules.whatsapp.models import WhatsAppMessage

pytestmark = pytest.mark.asyncio(loop_scope="session")

# Midday in India, so a few hours either way stay on the same day.
NOW = datetime(2026, 11, 2, 6, 30, tzinfo=UTC)
INTERVAL = 30


def phone() -> str:
    return f"+9198{uuid.uuid4().int % 10**8:08d}"


def live_code(secret: str, at: datetime = NOW, slots_ago: int = 0) -> str:
    return verifier.code_at(secret, verifier.slot_of(at.timestamp(), INTERVAL) - slots_ago)


def wrong_code(secret: str, at: datetime = NOW) -> str:
    """A code matching no slot in the last hour or the accepted window."""
    current = verifier.slot_of(at.timestamp(), INTERVAL)
    real = {verifier.code_at(secret, current - n) for n in range(-1, 122)}
    return next(code for n in range(1000) if (code := f"{n:06d}") not in real)


async def add_venue(session: AsyncSession, **fields) -> tuple[Venue, str]:
    """A live venue with a paired counter phone. Returns it with the phone's secret."""
    code = "V" + uuid.uuid4().hex[:8].upper()
    city = City(name="Ahmedabad", slug=f"ahd-{uuid.uuid4().hex[:6]}", state="Gujarat")
    session.add(city)
    await session.flush()
    venue = Venue(
        city_id=city.id,
        name="Kahwa House",
        slug="kahwa-house",
        scan_code=code,
        status=VenueStatus.COUNTER_LIVE,
        **({"category": "cafe"} | fields),
    )
    session.add(venue)
    await session.flush()
    secret = new_totp_secret()
    session.add(
        CounterDevice(
            venue_id=venue.id,
            totp_secret_enc=crypto.encrypt(secret),
            token_hash=uuid.uuid4().hex,
            paired_at=NOW,
        )
    )
    await session.flush()
    return venue, secret


async def add_message(session: AsyncSession, user: User) -> WhatsAppMessage:
    # Already handled: the race tests really commit, and the sweep tests must not find these.
    message = WhatsAppMessage(
        wa_message_id=f"wamid.{uuid.uuid4().hex}",
        direction="in",
        phone_e164=user.phone_e164,
        message_type="text",
        status="received",
        processed_at=NOW,
    )
    session.add(message)
    await session.flush()
    return message


@pytest.fixture
async def customer(db: AsyncSession) -> User:
    return await find_or_create_customer(db, phone(), "Riya Mehta")


@pytest.fixture
async def live(db: AsyncSession) -> tuple[Venue, str]:
    return await add_venue(db)


async def send(
    db: AsyncSession,
    redis,
    user: User,
    venue: Venue,
    code: str,
    *,
    now: datetime = NOW,
    scan_code: str | None = None,
) -> ScanResult:
    message = await add_message(db, user)
    scan = Scan(scan_code=scan_code or venue.scan_code, live_code=code)
    return await ScanService(db, redis).record_scan(user, message.id, scan, now=now)


async def points_of(db: AsyncSession, user: User) -> int:
    total = await db.scalar(
        select(func.sum(PointsEntry.points)).where(PointsEntry.user_id == user.id)
    )
    return total or 0


# --- a counted visit ------------------------------------------------------------------------------


async def test_a_good_scan_counts_a_visit_with_points(db, redis, customer, live) -> None:
    venue, secret = live

    result = await send(db, redis, customer, venue, live_code(secret))

    assert result.verified and result.reject_reason is None
    assert result.points_earned == 12  # +10 visit, +2 first time here
    visit = result.visit
    assert visit is not None
    assert (visit.user_id, visit.venue_id) == (customer.id, venue.id)
    assert (visit.notch_no, visit.visit_no_at_venue) == (1, 1)
    assert visit.visit_date == date(2026, 11, 2)
    assert visit.code_slot == verifier.slot_of(NOW.timestamp(), INTERVAL)
    assert visit.scan_session_id == result.scan_session.id
    assert result.scan_session.counter_device_id is not None
    assert await points_of(db, customer) == 12


async def test_first_scan_sets_home_city_and_last_seen(db, redis, customer, live) -> None:
    venue, secret = live

    await send(db, redis, customer, venue, live_code(secret))

    assert customer.city_id == venue.city_id
    assert customer.last_seen_at == NOW


async def test_home_city_is_not_overwritten(db, redis, customer, live) -> None:
    first, first_secret = live
    second, second_secret = await add_venue(db)

    await send(db, redis, customer, first, live_code(first_secret))
    await send(db, redis, customer, second, live_code(second_secret))

    assert customer.city_id == first.city_id


async def test_points_over_repeat_visits(db, redis, customer, live) -> None:
    venue, secret = live
    earned = []
    for day in range(6):
        at = NOW + timedelta(days=day)
        result = await send(db, redis, customer, venue, live_code(secret, at), now=at)
        assert result.visit is not None and result.visit.visit_no_at_venue == day + 1
        earned.append(result.points_earned)

    assert earned == [12, 10, 12, 10, 10, 12]
    assert await points_of(db, customer) == 66


async def test_notch_numbers_run_per_venue(db, redis, live) -> None:
    venue, secret = live
    other, other_secret = await add_venue(db, category="restaurant")  # a café was earlier today
    first = await find_or_create_customer(db, phone(), "A")
    second = await find_or_create_customer(db, phone(), "B")

    one = await send(db, redis, first, venue, live_code(secret))
    two = await send(db, redis, second, venue, live_code(secret))
    elsewhere = await send(db, redis, first, other, live_code(other_secret))

    assert [one.visit.notch_no, two.visit.notch_no, elsewhere.visit.notch_no] == [1, 2, 1]


async def test_unsaved_name_survives_the_customer_lock(db, redis, live) -> None:
    venue, secret = live
    number = phone()
    await find_or_create_customer(db, number, None)
    user = await find_or_create_customer(db, number, "Riya Mehta")  # name set, not yet saved

    await send(db, redis, user, venue, live_code(secret))

    assert user.name == "Riya Mehta"


# --- rejections -----------------------------------------------------------------------------------


async def assert_rejected(db, result: ScanResult, reason: RejectReason) -> None:
    assert not result.verified
    assert result.reject_reason == reason
    assert result.visit is None and result.points_earned == 0
    saved = await db.get(ScanSession, result.scan_session.id)
    assert saved is not None
    assert (saved.status, saved.reject_reason) == (ScanStatus.REJECTED, reason)


async def test_blocked_customer(db, redis, customer, live) -> None:
    venue, secret = live
    customer.status = UserStatus.BLOCKED

    result = await send(db, redis, customer, venue, live_code(secret))

    await assert_rejected(db, result, RejectReason.BLOCKED_CUSTOMER)


async def test_deleted_customer(db, redis, customer, live) -> None:
    venue, secret = live
    customer.deleted_at = NOW

    result = await send(db, redis, customer, venue, live_code(secret))

    await assert_rejected(db, result, RejectReason.BLOCKED_CUSTOMER)


async def test_unknown_venue_is_logged_without_a_venue(db, redis, customer, live) -> None:
    venue, secret = live

    result = await send(db, redis, customer, venue, live_code(secret), scan_code="NOSUCHCODE")

    await assert_rejected(db, result, RejectReason.UNKNOWN_VENUE)
    assert result.scan_session.venue_id is None
    assert result.scan_session.scan_code == "NOSUCHCODE"


@pytest.mark.parametrize("status", [VenueStatus.PAUSED, VenueStatus.CLOSED, VenueStatus.SIGNED])
async def test_venue_not_live(db, redis, customer, live, status) -> None:
    venue, secret = live
    venue.status = status
    await db.flush()

    result = await send(db, redis, customer, venue, live_code(secret))

    await assert_rejected(db, result, RejectReason.VENUE_UNAVAILABLE)
    assert result.scan_session.venue_id == venue.id


async def test_deleted_venue(db, redis, customer, live) -> None:
    venue, secret = live
    venue.deleted_at = NOW
    await db.flush()

    result = await send(db, redis, customer, venue, live_code(secret))

    await assert_rejected(db, result, RejectReason.VENUE_UNAVAILABLE)


@pytest.mark.parametrize(
    "change",
    [
        {"revoked_at": NOW, "is_active": False, "token_hash": None},
        {"token_hash": None},  # made, but the phone never paired
        {"is_active": False},
    ],
)
async def test_venue_without_a_working_counter(db, redis, customer, live, change) -> None:
    venue, secret = live
    counter = await db.scalar(select(CounterDevice).where(CounterDevice.venue_id == venue.id))
    for field, value in change.items():
        setattr(counter, field, value)
    await db.flush()

    result = await send(db, redis, customer, venue, live_code(secret))

    await assert_rejected(db, result, RejectReason.VENUE_UNAVAILABLE)


async def test_wrong_code(db, redis, customer, live) -> None:
    venue, secret = live

    result = await send(db, redis, customer, venue, wrong_code(secret))

    await assert_rejected(db, result, RejectReason.WRONG_CODE)
    assert customer.city_id is None  # only a counted visit sets the home city
    assert customer.last_seen_at == NOW


async def test_another_venues_code_is_wrong(db, redis, customer, live) -> None:
    venue, _ = live
    _, other_secret = await add_venue(db)

    result = await send(db, redis, customer, venue, live_code(other_secret))

    await assert_rejected(db, result, RejectReason.WRONG_CODE)


async def test_expired_code(db, redis, customer, live) -> None:
    venue, secret = live
    too_old = live_code(secret, slots_ago=verifier.SLOTS_BEFORE + 1)

    result = await send(db, redis, customer, venue, too_old)

    await assert_rejected(db, result, RejectReason.EXPIRED_CODE)


async def test_oldest_accepted_code_still_counts(db, redis, customer, live) -> None:
    venue, secret = live

    result = await send(db, redis, customer, venue, live_code(secret, slots_ago=2))

    assert result.verified


async def test_too_many_wrong_codes_lock_out_even_the_right_one(db, redis, customer, live) -> None:
    venue, secret = live
    for _ in range(MAX_WRONG_CODES):
        result = await send(db, redis, customer, venue, wrong_code(secret))
        await assert_rejected(db, result, RejectReason.WRONG_CODE)

    result = await send(db, redis, customer, venue, live_code(secret))

    await assert_rejected(db, result, RejectReason.WRONG_CODE)


async def test_expired_codes_use_no_guesses(db, redis, customer, live) -> None:
    venue, secret = live
    too_old = live_code(secret, slots_ago=verifier.SLOTS_BEFORE + 1)
    for _ in range(MAX_WRONG_CODES + 1):
        await send(db, redis, customer, venue, too_old)

    result = await send(db, redis, customer, venue, live_code(secret))

    assert result.verified


async def test_wrong_code_guesses_are_per_customer(db, redis, customer, live) -> None:
    venue, secret = live
    for _ in range(MAX_WRONG_CODES):
        await send(db, redis, customer, venue, wrong_code(secret))
    someone_else = await find_or_create_customer(db, phone(), "Asha")

    result = await send(db, redis, someone_else, venue, live_code(secret))

    assert result.verified


async def test_second_scan_same_day_is_refused_without_points(db, redis, customer, live) -> None:
    venue, secret = live
    await send(db, redis, customer, venue, live_code(secret))

    result = await send(db, redis, customer, venue, live_code(secret))

    await assert_rejected(db, result, RejectReason.ALREADY_TODAY)
    assert await points_of(db, customer) == 12


async def test_already_today_wins_over_a_used_up_code(db, redis, customer, live) -> None:
    venue, secret = live
    await send(db, redis, customer, venue, live_code(secret))
    for _ in range(MAX_PEOPLE_PER_CODE - 1):
        other = await find_or_create_customer(db, phone(), "Asha")
        await send(db, redis, other, venue, live_code(secret))

    result = await send(db, redis, customer, venue, live_code(secret))

    await assert_rejected(db, result, RejectReason.ALREADY_TODAY)


async def test_the_day_turns_at_midnight_in_india(db, redis, customer, live) -> None:
    venue, secret = live
    before = datetime(2026, 11, 2, 18, 29, tzinfo=UTC)  # 23:59 in India
    after = datetime(2026, 11, 2, 18, 31, tzinfo=UTC)  # 00:01 the next day

    first = await send(db, redis, customer, venue, live_code(secret, before), now=before)
    second = await send(db, redis, customer, venue, live_code(secret, after), now=after)

    assert first.visit.visit_date == date(2026, 11, 2)
    assert second.visit.visit_date == date(2026, 11, 3)


async def test_one_notch_per_niche_per_day(db, redis, customer) -> None:
    cafe, secret = await add_venue(db)
    assert (await send(db, redis, customer, cafe, live_code(secret))).verified
    other_cafe, other_secret = await add_venue(db)

    result = await send(db, redis, customer, other_cafe, live_code(other_secret))

    await assert_rejected(db, result, RejectReason.DAILY_LIMIT)
    assert await points_of(db, customer) == 12
    tomorrow = NOW + timedelta(days=1)
    code = live_code(other_secret, tomorrow)
    next_day = await send(db, redis, customer, other_cafe, code, now=tomorrow)
    assert next_day.verified


async def test_other_niches_count_the_same_day(db, redis, customer) -> None:
    for category in ("cafe", "restaurant", "turf_gym"):
        venue, secret = await add_venue(db, category=category)
        assert (await send(db, redis, customer, venue, live_code(secret))).verified

    assert await points_of(db, customer) == 3 * 12


async def test_a_revoked_notch_frees_the_niche(db, redis, customer) -> None:
    cafe, secret = await add_venue(db)
    first = await send(db, redis, customer, cafe, live_code(secret))
    first.visit.status = VisitStatus.REVOKED
    await db.flush()
    other_cafe, other_secret = await add_venue(db)

    result = await send(db, redis, customer, other_cafe, live_code(other_secret))

    assert result.verified


async def test_one_code_is_shared_by_a_limited_number_of_people(db, redis, live) -> None:
    venue, secret = live
    for _ in range(MAX_PEOPLE_PER_CODE):
        user = await find_or_create_customer(db, phone(), "Asha")
        assert (await send(db, redis, user, venue, live_code(secret))).verified
    late = await find_or_create_customer(db, phone(), "Asha")

    result = await send(db, redis, late, venue, live_code(secret))

    await assert_rejected(db, result, RejectReason.CODE_USED)
    later = NOW + timedelta(seconds=INTERVAL)
    next_code = await send(db, redis, late, venue, live_code(secret, later), now=later)
    assert next_code.verified


# --- the same message twice -----------------------------------------------------------------------


async def test_a_retried_verified_message_returns_the_first_result(
    db, redis, customer, live
) -> None:
    venue, secret = live
    message = await add_message(db, customer)
    scan = Scan(venue.scan_code, live_code(secret))
    service = ScanService(db, redis)

    first = await service.record_scan(customer, message.id, scan, now=NOW)
    again = await service.record_scan(customer, message.id, scan, now=NOW + timedelta(minutes=5))

    assert again.scan_session.id == first.scan_session.id
    assert again.visit.id == first.visit.id
    assert again.points_earned == 12
    assert await points_of(db, customer) == 12
    visits = await db.scalar(
        select(func.count()).select_from(Visit).where(Visit.user_id == customer.id)
    )
    assert visits == 1


async def test_a_retried_rejected_message_stays_rejected(db, redis, customer, live) -> None:
    venue, secret = live
    message = await add_message(db, customer)
    service = ScanService(db, redis)

    first = await service.record_scan(
        customer, message.id, Scan(venue.scan_code, wrong_code(secret)), now=NOW
    )
    again = await service.record_scan(
        customer, message.id, Scan(venue.scan_code, live_code(secret)), now=NOW
    )

    assert again.scan_session.id == first.scan_session.id
    assert again.reject_reason == RejectReason.WRONG_CODE


# --- scans arriving at once (separate connections, really committed) ------------------------------


@pytest.fixture
def sessions(db_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False, autoflush=False)


async def scan_in_own_transaction(sessions, redis, user_id, message_id, scan) -> ScanResult:
    async with sessions() as session:
        user = await session.get(User, user_id)
        result = await ScanService(session, redis).record_scan(user, message_id, scan, now=NOW)
        await session.commit()
        return result


async def test_two_messages_at_once_make_one_visit(sessions, redis) -> None:
    async with sessions() as session:
        venue, secret = await add_venue(session)
        user = await find_or_create_customer(session, phone(), "Riya Mehta")
        messages = [await add_message(session, user) for _ in range(2)]
        await session.commit()
    scan = Scan(venue.scan_code, live_code(secret))

    results = await asyncio.gather(
        *(scan_in_own_transaction(sessions, redis, user.id, m.id, scan) for m in messages)
    )

    assert sorted(r.verified for r in results) == [False, True]
    assert {r.reject_reason for r in results} == {None, RejectReason.ALREADY_TODAY}
    async with sessions() as session:
        assert await points_of(session, user) == 12


async def test_the_same_message_twice_at_once_is_recorded_once(sessions, redis) -> None:
    async with sessions() as session:
        venue, secret = await add_venue(session)
        user = await find_or_create_customer(session, phone(), "Riya Mehta")
        message = await add_message(session, user)
        await session.commit()
    scan = Scan(venue.scan_code, live_code(secret))

    results = await asyncio.gather(
        *(scan_in_own_transaction(sessions, redis, user.id, message.id, scan) for _ in range(2))
    )

    assert results[0].scan_session.id == results[1].scan_session.id
    assert results[0].visit.id == results[1].visit.id
    async with sessions() as session:
        assert await points_of(session, user) == 12


async def test_many_customers_at_once_get_distinct_notches_up_to_the_cap(sessions, redis) -> None:
    people = MAX_PEOPLE_PER_CODE + 2
    async with sessions() as session:
        venue, secret = await add_venue(session)
        users = [await find_or_create_customer(session, phone(), "Asha") for _ in range(people)]
        messages = [await add_message(session, user) for user in users]
        await session.commit()
    scan = Scan(venue.scan_code, live_code(secret))

    results = await asyncio.gather(
        *(
            scan_in_own_transaction(sessions, redis, user.id, message.id, scan)
            for user, message in zip(users, messages, strict=True)
        )
    )

    counted = [r for r in results if r.verified]
    assert sorted(r.visit.notch_no for r in counted) == list(range(1, MAX_PEOPLE_PER_CODE + 1))
    refused = [r.reject_reason for r in results if not r.verified]
    assert refused == [RejectReason.CODE_USED] * 2
