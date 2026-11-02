"""The admin panel's counts: the number next to each page's name."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.schemas import OverviewOut
from app.modules.jobs.models import JobRun, RunStatus
from app.modules.locations.time import today
from app.modules.notches.models import FraudSignal, SignalStatus
from app.modules.scans.models import COUNTED_STATUSES, Visit, VisitStatus
from app.modules.users.models import DeletionStatus, UserDeletion
from app.modules.venue_applications.models import ApplicationStatus, VenueApplication
from app.modules.venues.models import Venue


async def overview(session: AsyncSession) -> OverviewOut:
    async def count(model: Any, *conditions: Any) -> int:
        return await session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0

    by_status = dict(
        (
            await session.execute(
                select(Venue.status, func.count())
                .where(Venue.deleted_at.is_(None))
                .group_by(Venue.status)
            )
        ).all()
    )
    return OverviewOut(
        flagged_notches=await count(Visit, Visit.status == VisitStatus.FLAGGED),
        pending_applications=await count(
            VenueApplication, VenueApplication.status == ApplicationStatus.PENDING
        ),
        deletions_in_progress=await count(
            UserDeletion,
            UserDeletion.status.in_((DeletionStatus.AWAITING_YES, DeletionStatus.CONFIRMED)),
        ),
        open_fraud_signals=await count(FraudSignal, FraudSignal.status == SignalStatus.OPEN),
        failed_job_runs=await count(JobRun, JobRun.status == RunStatus.FAILED),
        venues_by_status=by_status,
        notches_today=await count(
            Visit, Visit.status.in_(COUNTED_STATUSES), Visit.visit_date == today(None)
        ),
    )
