"""What the check-in flow keeps in Abhishek's tables: points behind the rank, and deletion."""

from datetime import UTC, datetime, timedelta

from fakeredis import FakeAsyncRedis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.locations.time import last_days
from app.modules.notches.checkin import CheckInService, Outcome
from app.modules.rankings.service import standing
from app.modules.scans.models import PointsEntry, ScanSession, Visit
from app.modules.scans.parser import Scan
from app.modules.users.deletion import DeletionService, wipe_deleted
from app.modules.users.models import User
from tests import factories as f

NOW = datetime.now(UTC)


async def _venues(db: AsyncSession, count: int):
    city = await f.city(db)
    venues = [await f.venue(db, city) for _ in range(count)]
    for venue in venues:
        await f.counter(db, venue)
    return city, venues


async def test_a_notch_writes_a_scan_session_a_visit_and_points(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    _, (venue,) = await _venues(db, 1)
    user = await f.user(db)

    notch = await f.notch_at(db, redis, user, venue, NOW)

    session = await db.get(ScanSession, notch.scan_session_id)
    assert session is not None and session.status == "verified"
    entries = (await db.scalars(select(PointsEntry).where(PointsEntry.visit_id == notch.id))).all()
    assert sorted(e.reason for e in entries) == ["new_venue", "visit"]
    assert sum(e.points for e in entries) == 12


async def test_rank_follows_points_not_the_number_of_notchs(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    """Same two Notchs: two new places earn 24 points, the same place twice earns 22."""
    city, (one, two) = await _venues(db, 2)
    explorer = await f.user(db)
    regular = await f.user(db)
    # The regular gets there first, so only points can put the explorer ahead.
    await f.notch_at(db, redis, regular, one, NOW - timedelta(days=1, minutes=1))
    await f.notch_at(db, redis, regular, one, NOW - timedelta(minutes=1))
    await f.notch_at(db, redis, explorer, one, NOW - timedelta(days=1))
    await f.notch_at(db, redis, explorer, two, NOW)

    period = last_days(NOW.date(), 30)
    first = await standing(db, explorer.id, city.id, period)
    second = await standing(db, regular.id, city.id, period)
    assert first is not None and second is not None
    assert (first.rank, first.notches) == (1, 2)
    assert (second.rank, second.notches) == (2, 2)


async def test_a_scan_waiting_for_consent_records_nothing_until_agreed(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    _, (venue,) = await _venues(db, 1)
    user = await f.user(db, consented=False)
    service = CheckInService(db, redis)

    waiting = await service.scan(
        user,
        Scan(venue.scan_code, f.code_at(NOW)),
        scanned_at=NOW,
        whatsapp_message_id=await f.message_id(db),
    )
    assert waiting.outcome is Outcome.NEEDS_CONSENT
    assert await db.scalar(select(func.count()).select_from(ScanSession)) == 0

    done = await service.complete_pending(user)
    assert done is not None and done.outcome is Outcome.NOTCHED
    assert await db.scalar(select(func.count()).select_from(Visit)) == 1


async def test_deleting_a_customer_keeps_the_venues_totals_only(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    _, (venue,) = await _venues(db, 1)
    user = await f.user(db)
    notch = await f.notch_at(db, redis, user, venue, NOW)
    user_id, visit_id, session_id = user.id, notch.id, notch.scan_session_id

    deletions = DeletionService(db)
    await deletions.ask(user)
    assert await deletions.confirm(user) is True
    assert await wipe_deleted(db) >= 1

    assert await db.scalar(select(func.count()).select_from(User).where(User.id == user_id)) == 0
    points = select(func.count()).select_from(PointsEntry).where(PointsEntry.user_id == user_id)
    assert await db.scalar(points) == 0
    await db.refresh(notch)
    assert notch.id == visit_id and notch.user_id is None
    session = await db.get(ScanSession, session_id)
    await db.refresh(session)
    assert session.user_id is None
