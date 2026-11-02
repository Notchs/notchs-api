"""`/admin/audit-log` (G6) and `/admin/overview` (the admin panel's counts)."""

import uuid

from fastapi import APIRouter

from app.common.dependencies import DBSession, Pagination
from app.common.schemas import CustomResponse, PaginatedResponse
from app.modules.audit import service
from app.modules.audit.models import ActorType
from app.modules.audit.overview import overview as build_overview
from app.modules.audit.schemas import AuditEntryOut, OverviewOut
from app.modules.auth.dependencies import CurrentAdmin

router = APIRouter(prefix="/admin", tags=["Admin: audit log"])


@router.get(
    "/audit-log",
    response_model=CustomResponse[PaginatedResponse[AuditEntryOut]],
    summary="Who changed what, newest first (G6)",
)
async def audit_log(
    _: CurrentAdmin,
    session: DBSession,
    pagination: Pagination,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    actor_type: ActorType | None = None,
) -> CustomResponse[PaginatedResponse[AuditEntryOut]]:
    rows, total = await service.entries(
        session, pagination, target_type=target_type, target_id=target_id, actor_type=actor_type
    )
    return CustomResponse(
        message="OK",
        data=PaginatedResponse.build(
            [AuditEntryOut.model_validate(r) for r in rows], total, pagination.page, pagination.size
        ),
    )


@router.get("/overview", response_model=CustomResponse[OverviewOut], summary="Counts for the nav")
async def overview(_: CurrentAdmin, session: DBSession) -> CustomResponse[OverviewOut]:
    return CustomResponse(message="OK", data=await build_overview(session))
