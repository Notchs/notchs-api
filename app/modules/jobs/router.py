"""`/admin/jobs` (G5): monthly job runs, their failures, and running a month again."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, status
from pydantic import Field

from app.common.dependencies import DBSession, Pagination
from app.common.schemas import BaseSchema, CustomResponse, PaginatedResponse
from app.db.session import get_session_factory
from app.modules.auth.dependencies import CurrentAdmin, CurrentFullAdmin
from app.modules.jobs import runner, service
from app.modules.jobs.models import JobName, RunStatus, RunTrigger
from app.modules.jobs.monthly import JOBS
from app.modules.jobs.runner import SessionFactory

router = APIRouter(prefix="/admin/jobs", tags=["Admin: monthly jobs"])

SessionFactoryDep = Annotated[SessionFactory, Depends(get_session_factory)]


class JobRunOut(BaseSchema):
    id: uuid.UUID
    job: JobName
    period: str
    status: RunStatus
    trigger: RunTrigger
    started_at: datetime
    finished_at: datetime | None
    total: int
    done: int
    failed: int
    errors: list[dict[str, Any]]


class RunJobIn(BaseSchema):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", examples=["2026-10"])


@router.get(
    "",
    response_model=CustomResponse[PaginatedResponse[JobRunOut]],
    summary="Monthly job runs, newest first (G5)",
)
async def runs(
    _: CurrentAdmin, session: DBSession, pagination: Pagination
) -> CustomResponse[PaginatedResponse[JobRunOut]]:
    rows, total = await service.list_runs(session, pagination)
    return CustomResponse(
        message="OK",
        data=PaginatedResponse.build(
            [JobRunOut.model_validate(r) for r in rows], total, pagination.page, pagination.size
        ),
    )


@router.post(
    "/{job}/run",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CustomResponse[None],
    summary="Run a month (again). Finishes only what is not done yet",
)
async def run_job(
    job: JobName,
    body: RunJobIn,
    admin: CurrentFullAdmin,
    session: DBSession,
    session_factory: SessionFactoryDep,
    background: BackgroundTasks,
) -> CustomResponse[None]:
    run_id = await service.start(session, session_factory, job, body.period, admin)
    background.add_task(runner.execute, session_factory, run_id, JOBS[job], body.period)
    return CustomResponse(message="Started. Refresh to see progress.")
