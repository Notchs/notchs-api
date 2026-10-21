"""`/admin/audit-logs`: the activity history of the whole platform, for the admin panel.

Needs the `audit.view` permission.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.dependencies import DBSession, Pagination
from app.common.schemas import CustomResponse, PaginatedResponse
from app.modules.admin_users.models import AdminUser
from app.modules.admin_users.permissions import Permission
from app.modules.audit.models import ActorType
from app.modules.audit.schemas import AuditLogOut
from app.modules.audit.service import AuditFilters, AuditLogService
from app.modules.auth.dependencies import require_permission

router = APIRouter(prefix="/admin/audit-logs", tags=["Admin: audit log"])


def get_audit_log_service(session: DBSession) -> AuditLogService:
    return AuditLogService(session)


AuditLogServiceDep = Annotated[AuditLogService, Depends(get_audit_log_service)]
AuditViewer = Annotated[AdminUser, Depends(require_permission(Permission.AUDIT_VIEW))]


@router.get(
    "",
    response_model=CustomResponse[PaginatedResponse[AuditLogOut]],
    summary="Activity history, newest first",
)
async def list_audit_logs(
    _: AuditViewer,
    service: AuditLogServiceDep,
    pagination: Pagination,
    actor_type: ActorType | None = None,
    actor_id: uuid.UUID | None = None,
    action: Annotated[
        str | None,
        Query(max_length=40, description='Exact, or a prefix ending in "." such as "auth."'),
    ] = None,
    entity_type: Annotated[str | None, Query(max_length=30, description="Table name")] = None,
    entity_id: Annotated[str | None, Query(max_length=40)] = None,
    since: Annotated[datetime | None, Query(description="From this time (inclusive)")] = None,
    until: Annotated[datetime | None, Query(description="Before this time")] = None,
) -> CustomResponse[PaginatedResponse[AuditLogOut]]:
    filters = AuditFilters(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        since=since,
        until=until,
    )
    items, total = await service.list(pagination, filters)
    page = PaginatedResponse.build(
        [AuditLogOut.model_validate(item) for item in items],
        total,
        pagination.page,
        pagination.size,
    )
    return CustomResponse(message="OK", data=page)
