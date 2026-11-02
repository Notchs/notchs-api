"""The background scheduler, started with the API (SCHEDULER_ENABLED). Every tick:

    every minute       wipe the data of closed accounts (users/deletion.py)
    every 5 minutes    "Counter phone is off" emails (alerts.py)
    the 1st, IST       06:00 customers' cards, 06:30 venues' cards

No paid WhatsApp template is ever sent from here: business-initiated messages ("Your card is
ready", B7) cost money and need Harsh's or Abhishek's approval first (CLAUDE.md).

A monthly job missed on the 1st (the API was down) runs on the next tick that finds it due:
each month is claimed once in job_runs, so restarts and several instances are safe.
"""

import asyncio
from datetime import UTC, datetime, time

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.modules.jobs import runner
from app.modules.jobs.alerts import alert_offline_counters
from app.modules.jobs.models import JobName, RunTrigger
from app.modules.jobs.monthly import JOBS
from app.modules.jobs.runner import SessionFactory
from app.modules.locations.time import local, previous_month
from app.modules.notches.checkin import delete_expired_pending
from app.modules.users.deletion import wipe_deleted

logger = get_logger(__name__)

MONTHLY_TIMES: dict[JobName, time] = {
    JobName.NOTCH_CARDS: time(6, 0),
    JobName.VENUE_CARDS: time(6, 30),
}
ALERT_EVERY_TICKS = 5


def period_label(moment: datetime) -> str:
    """The month a job running at `moment` (IST) is for: the one before."""
    month = previous_month(local(moment, None).date())
    return f"{month.start.year}-{month.start.month:02d}"


def is_due(job: JobName, moment: datetime) -> bool:
    at = local(moment, None)
    return at.day > 1 or at.time() >= MONTHLY_TIMES[job]


async def run_monthly(session_factory: SessionFactory, moment: datetime | None = None) -> None:
    moment = moment or datetime.now(UTC)
    period = period_label(moment)
    for job in MONTHLY_TIMES:
        if not is_due(job, moment):
            continue
        run_id = await runner.claim(session_factory, job, period, trigger=RunTrigger.SCHEDULER)
        if run_id is not None:
            logger.info("Starting monthly job %s for %s", job, period)
            await runner.execute(session_factory, run_id, JOBS[job], period)


async def tick(session_factory: SessionFactory, number: int) -> None:
    async with session_factory() as session:
        wiped = await wipe_deleted(session)
        await delete_expired_pending(session)
        await session.commit()
    if wiped:
        logger.info("Wiped the data of %d closed account(s)", wiped)
    if number % ALERT_EVERY_TICKS == 0:
        await alert_offline_counters(session_factory)
    await run_monthly(session_factory)


async def run_scheduler(
    interval_seconds: float, session_factory: SessionFactory | None = None
) -> None:
    """Background loop: a tick now, then every `interval_seconds`. One failing step is logged
    and retried next tick; it never stops the loop."""
    factory = session_factory or AsyncSessionLocal
    number = 0
    while True:
        try:
            await tick(factory, number)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduler tick failed; trying again next round")
        number += 1
        await asyncio.sleep(interval_seconds)
