"""Reading job runs and starting one from the admin panel (G5)."""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.dependencies import PaginationParams
from app.modules.admin_users.models import AdminUser
from app.modules.audit import service as audit
from app.modules.jobs import runner
from app.modules.jobs.exceptions import JobAlreadyRunningException
from app.modules.jobs.models import JobName, JobRun, RunTrigger
from app.modules.jobs.runner import SessionFactory


async def list_runs(
    session: AsyncSession, params: PaginationParams
) -> tuple[Sequence[JobRun], int]:
    rows = (
        await session.scalars(
            select(JobRun)
            .order_by(JobRun.started_at.desc(), JobRun.id)
            .offset(params.offset)
            .limit(params.size)
        )
    ).all()
    total = await session.scalar(select(func.count()).select_from(JobRun))
    return rows, total or 0


async def start(
    session: AsyncSession,
    session_factory: SessionFactory,
    job: JobName,
    period: str,
    admin: AdminUser,
) -> uuid.UUID:
    """Claim the run (a re-run takes over a finished one) and audit it. The caller executes it
    in the background. Raises JobAlreadyRunningException if it is running now."""
    try:
        run_id = await runner.claim(
            session_factory, job, period, trigger=RunTrigger.ADMIN, admin_id=admin.id, rerun=True
        )
    except runner.JobAlreadyRunningError as exc:
        raise JobAlreadyRunningException() from exc
    assert run_id is not None  # a re-run always gets the row
    await audit.record(
        session,
        actor=admin,
        action="job.run",
        target_type="job_run",
        target_id=run_id,
        target_label=f"{job} · {period}",
    )
    return run_id
