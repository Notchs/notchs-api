"""`/admin/users` (G3): find a customer by number or code, see their visits, block them, and
handle deletion requests. Phone numbers are shown masked; opening a customer is audited."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.common.dependencies import DBSession, Pagination
from app.common.schemas import CustomResponse, PaginatedResponse
from app.modules.auth.dependencies import CurrentAdmin, CurrentFullAdmin
from app.modules.users.admin_schemas import AdminUserOut, AdminUserRowOut, DeletionOut
from app.modules.users.admin_service import UserAdminService
from app.modules.users.models import DeletionStatus, UserStatus

router = APIRouter(prefix="/admin", tags=["Admin: users"])


def get_user_admin(session: DBSession) -> UserAdminService:
    return UserAdminService(session)


UserAdminDep = Annotated[UserAdminService, Depends(get_user_admin)]


@router.get(
    "/users",
    response_model=CustomResponse[PaginatedResponse[AdminUserRowOut]],
    summary="Find customers by phone number, USR- code or name (G3)",
)
async def search(
    _: CurrentAdmin, service: UserAdminDep, pagination: Pagination, q: str | None = None
) -> CustomResponse[PaginatedResponse[AdminUserRowOut]]:
    items, total = await service.search(pagination, q)
    return CustomResponse(
        message="OK", data=PaginatedResponse.build(items, total, pagination.page, pagination.size)
    )


@router.get(
    "/users/{ref}",
    response_model=CustomResponse[AdminUserOut],
    summary="One customer and their latest visits. Recorded in the audit log",
)
async def get_user(
    ref: str, admin: CurrentAdmin, service: UserAdminDep
) -> CustomResponse[AdminUserOut]:
    return CustomResponse(message="OK", data=await service.look_up(ref, admin))


@router.post(
    "/users/{ref}/block",
    response_model=CustomResponse[AdminUserOut],
    summary="Block: no more Notchs, logins or bot replies",
)
async def block(
    ref: str, admin: CurrentFullAdmin, service: UserAdminDep
) -> CustomResponse[AdminUserOut]:
    return CustomResponse(
        message="Blocked.", data=await service.set_status(ref, admin, UserStatus.BLOCKED)
    )


@router.post("/users/{ref}/unblock", response_model=CustomResponse[AdminUserOut], summary="Unblock")
async def unblock(
    ref: str, admin: CurrentFullAdmin, service: UserAdminDep
) -> CustomResponse[AdminUserOut]:
    return CustomResponse(
        message="Unblocked.", data=await service.set_status(ref, admin, UserStatus.ACTIVE)
    )


@router.post(
    "/users/{ref}/delete",
    response_model=CustomResponse[DeletionOut],
    summary="Delete a customer's data for them (a request by email, say). Wiped within a minute",
)
async def delete_user(
    ref: str, admin: CurrentFullAdmin, service: UserAdminDep
) -> CustomResponse[DeletionOut]:
    return CustomResponse(
        message="The customer's data will be wiped within a minute.",
        data=await service.delete(ref, admin),
    )


@router.get(
    "/user-deletions",
    response_model=CustomResponse[PaginatedResponse[DeletionOut]],
    summary="Deletion requests, newest first: waiting for YES, confirmed, done (G3)",
)
async def deletions(
    _: CurrentAdmin,
    service: UserAdminDep,
    pagination: Pagination,
    status: DeletionStatus | None = None,
) -> CustomResponse[PaginatedResponse[DeletionOut]]:
    items, total = await service.deletions(pagination, status)
    return CustomResponse(
        message="OK",
        data=PaginatedResponse.build(list(items), total, pagination.page, pagination.size),
    )
