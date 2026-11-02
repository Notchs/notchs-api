"""The QR spec's verification rules, against a real Postgres (TEST_DATABASE_URL)."""

from datetime import UTC, datetime, timedelta

from fakeredis import FakeAsyncRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notches.checkin import CheckInService, Outcome
from app.modules.notches.codes import ScanMessage
from app.modules.notches.models import (
    Badge,
    FraudSignal,
    NotchFlag,
    NotchStatus,
    SignalKind,
    UserBadge,
)
from app.modules.venues.models import VenueStatus
from tests import factories as f


async def _setup(db: AsyncSession, **counter_kwargs: object):
    city = await f.city(db)
    area = await f.area(db, city)
    venue = await f.venue(db, city, name="Kahwa House", area_row=area)
    counter = await f.counter(db, venue, **counter_kwargs)
    return city, venue, counter


def _scan(venue, moment: datetime, offset: int = 0) -> ScanMessage:
    return ScanMessage(venue.venue_code, f.code_at(moment, offset=offset))


async def test_a_fresh_code_notches(db: AsyncSession, redis: FakeAsyncRedis) -> None:
    _, venue, counter = await _setup(db)
    user = await f.user(db)
    now = datetime.now(UTC)

    result = await CheckInService(db, redis).scan(user, _scan(venue, now), scanned_at=now)

    assert result.outcome is Outcome.NOTCHED
    notch = result.notch
    assert notch is not None
    assert (notch.notch_no, notch.user_visit_no, notch.status) == (1, 1, NotchStatus.VERIFIED)
    assert notch.flags == []
    assert venue.notch_count == 1
    assert counter.last_scan_at == now
    assert result.visits_this_month == 1
    # First ever Notch: Founding Member #1 is announced, and First Notch earned here.
    assert result.announce_founding_no == 1
    assert user.founding_member_no == 1
    assert result.new_badges == [Badge.FIRST_NOTCH]


async def test_the_previous_and_next_windows_are_accepted(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    _, venue, _ = await _setup(db)
    now = datetime.now(UTC)
    service = CheckInService(db, redis)

    previous = await service.scan(await f.user(db), _scan(venue, now, -1), scanned_at=now)
    following = await service.scan(await f.user(db), _scan(venue, now, 1), scanned_at=now)

    assert previous.outcome is Outcome.NOTCHED and previous.notch.code_offset == -1
    assert following.outcome is Outcome.NOTCHED and following.notch.code_offset == 1
    assert following.notch.notch_no == 2


async def test_an_older_code_has_expired(db: AsyncSession, redis: FakeAsyncRedis) -> None:
    _, venue, _ = await _setup(db)
    now = datetime.now(UTC)

    result = await CheckInService(db, redis).scan(
        await f.user(db), _scan(venue, now, -2), scanned_at=now
    )

    assert result.outcome is Outcome.EXPIRED


async def test_a_wrong_code_or_unknown_venue_is_expired(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    _, venue, _ = await _setup(db)
    now = datetime.now(UTC)
    service = CheckInService(db, redis)
    user = await f.user(db)

    wrong = str((int(f.code_at(now)) + 1) % 1_000_000).zfill(6)
    assert (
        await service.scan(user, ScanMessage(venue.venue_code, wrong), scanned_at=now)
    ).outcome is Outcome.EXPIRED
    unknown = ScanMessage("VEN-00000000", f.code_at(now))
    assert (await service.scan(user, unknown, scanned_at=now)).outcome is Outcome.EXPIRED


async def test_one_notch_per_venue_per_day(db: AsyncSession, redis: FakeAsyncRedis) -> None:
    _, venue, _ = await _setup(db)
    user = await f.user(db)
    service = CheckInService(db, redis)
    now = datetime.now(UTC)
    later = now + timedelta(hours=1)

    await service.scan(user, _scan(venue, now), scanned_at=now)
    again = await service.scan(user, _scan(venue, later), scanned_at=later)

    assert again.outcome is Outcome.ALREADY_TODAY
    assert venue.notch_count == 1


async def test_days_follow_the_city_clock(db: AsyncSession, redis: FakeAsyncRedis) -> None:
    _, venue, _ = await _setup(db)
    user = await f.user(db)
    service = CheckInService(db, redis)
    # 23:50 IST and 00:10 IST the next day: two different days, though one UTC date.
    late = datetime(2026, 10, 20, 18, 20, tzinfo=UTC)
    after_midnight = late + timedelta(minutes=20)

    first = await service.scan(user, _scan(venue, late), scanned_at=late)
    second = await service.scan(user, _scan(venue, after_midnight), scanned_at=after_midnight)

    assert first.notch.local_date.isoformat() == "2026-10-20"
    assert first.notch.local_hour == 23
    assert second.outcome is Outcome.NOTCHED
    assert second.notch.local_date.isoformat() == "2026-10-21"
    assert second.notch.user_visit_no == 2


async def test_offline_counter_accepts_two_windows_and_flags(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    _, venue, _ = await _setup(db, last_seen=f.ago(minutes=10))
    now = datetime.now(UTC)

    result = await CheckInService(db, redis).scan(
        await f.user(db), _scan(venue, now, -2), scanned_at=now
    )

    assert result.outcome is Outcome.NOTCHED
    assert result.notch.status == NotchStatus.FLAGGED
    assert result.notch.flags == [NotchFlag.COUNTER_OFFLINE]
    signal = await db.scalar(select(FraudSignal))
    assert signal is not None and signal.kind == SignalKind.COUNTER_OFFLINE


async def test_a_shared_code_flags_every_notch_made_with_it(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    _, venue, _ = await _setup(db)
    now = datetime.now(UTC)
    service = CheckInService(db, redis)
    scan = _scan(venue, now)

    first = await service.scan(await f.user(db), scan, scanned_at=now)
    second = await service.scan(await f.user(db), scan, scanned_at=now + timedelta(seconds=4))
    await db.flush()

    for result in (first, second):
        assert result.outcome is Outcome.NOTCHED  # both still count
        assert result.notch.status == NotchStatus.FLAGGED
        assert NotchFlag.SHARED_CODE in result.notch.flags
    signal = await db.scalar(select(FraudSignal).where(FraudSignal.kind == SignalKind.SHARED_CODE))
    assert signal is not None and signal.details["numbers"] == 2


async def test_many_scans_in_a_minute_raise_a_signal(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    _, venue, _ = await _setup(db)
    service = CheckInService(db, redis)
    minute = datetime.now(UTC).replace(second=1, microsecond=0)

    for i in range(7):
        moment = minute + timedelta(seconds=i)
        await service.scan(await f.user(db), _scan(venue, moment), scanned_at=moment)

    signal = await db.scalar(
        select(FraudSignal).where(FraudSignal.kind == SignalKind.MANY_SCANS_MINUTE)
    )
    assert signal is not None and signal.details["scans"] == 7


async def test_first_scan_waits_for_consent(db: AsyncSession, redis: FakeAsyncRedis) -> None:
    _, venue, _ = await _setup(db)
    user = await f.user(db, consented=False)
    service = CheckInService(db, redis)
    now = datetime.now(UTC)

    waiting = await service.scan(user, _scan(venue, now), scanned_at=now)
    assert waiting.outcome is Outcome.NEEDS_CONSENT
    assert venue.notch_count == 0

    user.consent_at = datetime.now(UTC)
    done = await service.complete_pending(user)
    assert done is not None and done.outcome is Outcome.NOTCHED
    assert done.notch.scanned_at == now  # the original scan time, even if the code has expired
    assert await service.complete_pending(user) is None


async def test_paused_venues_do_not_notch(db: AsyncSession, redis: FakeAsyncRedis) -> None:
    _, venue, _ = await _setup(db)
    venue.status = VenueStatus.PAUSED
    now = datetime.now(UTC)

    result = await CheckInService(db, redis).scan(
        await f.user(db), _scan(venue, now), scanned_at=now
    )

    assert result.outcome is Outcome.VENUE_NOT_LIVE


async def test_regular_after_five_visits_in_a_month(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    _, venue, _ = await _setup(db)
    user = await f.user(db)
    service = CheckInService(db, redis)
    start = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)

    results = []
    for day in range(5):
        moment = start + timedelta(days=day)
        results.append(await service.scan(user, _scan(venue, moment), scanned_at=moment))

    assert [r.visits_this_month for r in results] == [1, 2, 3, 4, 5]
    assert results[-1].new_badges == [Badge.REGULAR]
    assert results[-1].announce_founding_no is None  # only with the first Notch
    badges = set(await db.scalars(select(UserBadge.badge).where(UserBadge.user_id == user.id)))
    assert badges == {Badge.FIRST_NOTCH, Badge.REGULAR, Badge.FOUNDING_MEMBER}


async def test_first_notch_badge_only_for_the_first_customers(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    city = await f.city(db)
    venue = await f.venue(db, city, first_notch_spots=2)
    await f.counter(db, venue)
    service = CheckInService(db, redis)
    now = datetime.now(UTC)

    earned = []
    for _ in range(3):
        result = await service.scan(await f.user(db), _scan(venue, now), scanned_at=now)
        earned.append(Badge.FIRST_NOTCH in result.new_badges)

    assert earned == [True, True, False]
