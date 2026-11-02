"""Running a monthly job once per month, recording progress in `job_runs` (G5).

    claim    insert the (job, period) row; if it exists, only an admin re-run takes it over
             (and never while it is still running, unless it has been silent for STALE_AFTER)
    execute  run the job with a Progress the job reports to; failures per item are recorded
             (newest MAX_STORED_ERRORS kept) and never stop the rest
    finish   succeeded with no failures, failed otherwise

Jobs skip work already done, so a re-run finishes only what failed.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ids import new_uuid
from app.core.logging import get_logger
from app.modules.jobs.models import MAX_STORED_ERRORS, JobName, JobRun, RunStatus, RunTrigger

logger = get_logger(__name__)

SessionFactory = Callable[[], AsyncSession]
STALE_AFTER = timedelta(hours=2)
SAVE_EVERY = 25


class JobConfigError(Exception):
    """The job cannot run with the current settings. Its message is ours, so it is shown."""


def describe(error: BaseException | str) -> str:
    """What a failure is called in job_runs and the logs: our own messages as they are, any
    other exception by its type only. Database and provider errors can quote the data that
    failed (a phone number, an email), and personal data never goes into logs or job_runs."""
    if isinstance(error, str):
        return error
    if isinstance(error, JobConfigError):
        return str(error)
    return type(error).__name__


class JobAlreadyRunningError(Exception):
    pass


class Progress:
    """What a job reports while it runs. Saved to its job_runs row every SAVE_EVERY items."""

    def __init__(self, run_id: uuid.UUID, session_factory: SessionFactory) -> None:
        self.run_id = run_id
        self.session_factory = session_factory
        self.total = 0
        self.done = 0
        self.failed = 0
        self.errors: list[dict[str, str]] = []

    async def ok(self) -> None:
        self.done += 1
        await self._maybe_save()

    async def fail(self, ref: str, error: BaseException | str) -> None:
        """`ref` must be an id or public code, never personal data."""
        text = describe(error)[:300]
        self.failed += 1
        self.errors.append({"at": datetime.now(UTC).isoformat(), "ref": ref, "error": text})
        self.errors = self.errors[-MAX_STORED_ERRORS:]
        logger.warning("Job run %s: %s failed: %s", self.run_id, ref, text)
        await self._maybe_save()

    async def _maybe_save(self) -> None:
        if (self.done + self.failed) % SAVE_EVERY == 0:
            await self.save()

    async def save(self, *, status: RunStatus | None = None) -> None:
        async with self.session_factory() as session:
            run = await session.get(JobRun, self.run_id)
            if run is None:
                return
            run.total, run.done, run.failed = self.total, self.done, self.failed
            run.errors = list(self.errors)
            if status is not None:
                run.status = status
                run.finished_at = datetime.now(UTC)
            await session.commit()


JobFunction = Callable[[SessionFactory, str, Progress], Awaitable[None]]


async def claim(
    session_factory: SessionFactory,
    job: JobName,
    period: str,
    *,
    trigger: RunTrigger,
    admin_id: uuid.UUID | None = None,
    rerun: bool = False,
) -> uuid.UUID | None:
    """Take the (job, period) run. Returns its id, or None if it already ran and this is not
    a re-run. Raises JobAlreadyRunningError if a re-run finds it still running."""
    now = datetime.now(UTC)
    async with session_factory() as session:
        run_id = await session.scalar(
            insert(JobRun)
            .values(
                id=new_uuid(),
                job=job,
                period=period,
                status=RunStatus.RUNNING,
                trigger=trigger,
                triggered_by_admin_id=admin_id,
                started_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_job_runs_job_period")
            .returning(JobRun.id)
        )
        if run_id is None:
            if not rerun:
                return None
            run = await session.scalar(
                select(JobRun).where(JobRun.job == job, JobRun.period == period).with_for_update()
            )
            assert run is not None
            if run.status == RunStatus.RUNNING and now - run.started_at < STALE_AFTER:
                raise JobAlreadyRunningError()
            run.status = RunStatus.RUNNING
            run.trigger = trigger
            run.triggered_by_admin_id = admin_id
            run.started_at = now
            run.finished_at = None
            run.total = run.done = run.failed = 0
            run.errors = []
            run_id = run.id
        await session.commit()
        return run_id


async def execute(
    session_factory: SessionFactory, run_id: uuid.UUID, function: JobFunction, period: str
) -> RunStatus:
    progress = Progress(run_id, session_factory)
    try:
        await function(session_factory, period, progress)
    except JobConfigError as exc:
        await progress.fail("settings", exc)
    except Exception as exc:
        await progress.fail("job", f"Stopped: {describe(exc)}")
    status = RunStatus.SUCCEEDED if progress.failed == 0 else RunStatus.FAILED
    await progress.save(status=status)
    logger.info(
        "Job run %s finished: %s (%d done, %d failed of %d)",
        run_id,
        status,
        progress.done,
        progress.failed,
        progress.total,
    )
    return status
