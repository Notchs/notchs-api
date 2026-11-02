from datetime import UTC, datetime, timedelta

import pytest
from fakeredis import FakeAsyncRedis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.email import EmailClient, EmailSendError
from app.modules.jobs.alerts import alert_offline_counters
from app.modules.jobs.models import JobName, JobRun, RunStatus
from app.modules.jobs.runner import SessionFactory
from app.modules.jobs.scheduler import is_due, period_label, run_monthly, tick
from app.modules.users.models import User, UserStatus
from tests import factories as f


def ist(day: int, hour: int, minute: int = 0) -> datetime:
    """A moment in IST (UTC+5:30) in November 2026, as UTC."""
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC) - timedelta(hours=5, minutes=30)


def test_monthly_jobs_are_due_from_their_time_on_the_1st() -> None:
    assert period_label(ist(1, 6)) == "2026-10"
    assert not is_due(JobName.NOTCH_CARDS, ist(1, 5, 59))
    assert is_due(JobName.NOTCH_CARDS, ist(1, 6))
    assert not is_due(JobName.VENUE_CARDS, ist(1, 6, 10))
    assert is_due(JobName.VENUE_CARDS, ist(1, 6, 30))
    # Missed on the 1st (the API was down): due any later day of the month.
    assert is_due(JobName.VENUE_CARDS, ist(3, 1))


async def test_run_monthly_claims_each_month_once(
    db: AsyncSession, session_factory: SessionFactory
) -> None:
    await run_monthly(session_factory, ist(1, 6, 15))
    await run_monthly(session_factory, ist(1, 6, 16))

    runs = (await db.scalars(select(JobRun).order_by(JobRun.job))).all()
    # Venue cards are not due before 06:30.
    assert [(r.job, r.period, r.status) for r in runs] == [
        (JobName.NOTCH_CARDS, "2026-10", RunStatus.SUCCEEDED)
    ]


async def test_counter_off_alert_once_per_outage(
    db: AsyncSession, redis: FakeAsyncRedis, session_factory: SessionFactory
) -> None:
    city = await f.city(db)
    always_open = {
        day: [["00:00", "23:59"]] for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    }
    venue = await f.venue(db, city, opening_hours=always_open)
    counter = await f.counter(db, venue, last_seen=f.ago(minutes=30))
    await f.member(db, venue)
    no_hours = await f.venue(db, city)
    await f.counter(db, no_hours, last_seen=f.ago(minutes=30))

    assert await alert_offline_counters(session_factory) == 1
    assert await alert_offline_counters(session_factory) == 0  # already told
    await db.refresh(counter)
    assert counter.offline_alerted_at is not None


async def test_counter_off_email_failure_releases_the_claim(
    db: AsyncSession,
    redis: FakeAsyncRedis,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    city = await f.city(db)
    always_open = {
        day: [["00:00", "23:59"]] for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    }
    venue = await f.venue(db, city, opening_hours=always_open)
    counter = await f.counter(db, venue, last_seen=f.ago(minutes=30))
    await f.member(db, venue)

    async def down(_: object, __: object) -> None:
        raise EmailSendError("Resend returned 500")

    monkeypatch.setattr(EmailClient, "send", down)
    assert await alert_offline_counters(session_factory) == 0
    await db.refresh(counter)
    assert counter.offline_alerted_at is None  # the next round tries again


async def test_tick_wipes_closed_accounts(
    db: AsyncSession, session_factory: SessionFactory
) -> None:
    user = await f.user(db, status=UserStatus.DELETED, deleted_at=f.ago(minutes=1))

    user_id = user.id
    await tick(session_factory, 1)

    # Counted in the database, not read from the session's cache.
    left = await db.scalar(select(func.count()).select_from(User).where(User.id == user_id))
    assert left == 0
