"""`/admin/permissions` and `/admin/roles`: the roles screen of the admin panel."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.common.dependencies import DBSession
from app.common.schemas import CustomResponse
from app.modules.admin_users.models import AdminUser
from app.modules.admin_users.permissions import CATALOG, Permission
from app.modules.admin_users.roles import RoleService, RoleView
from app.modules.admin_users.schemas import PermissionOut, RoleCreate, RoleOut, RoleUpdate
from app.modules.auth.dependencies import CurrentAdmin, require_permission

router = APIRouter(prefix="/admin", tags=["Admin: roles"])

TeamViewer = Annotated[AdminUser, Depends(require_permission(Permission.ADMINS_VIEW))]
RoleManager = Annotated[AdminUser, Depends(require_permission(Permission.ROLES_MANAGE))]


def get_role_service(session: DBSession) -> RoleService:
    return RoleService(session)


RoleServiceDep = Annotated[RoleService, Depends(get_role_service)]


def _out(view: RoleView) -> RoleOut:
    role = view.role
    return RoleOut(
        id=role.id,
        admin_role_code=role.admin_role_code,
        name=role.name,
        description=role.description,
        is_owner=role.is_owner,
        permissions=sorted(view.permissions),
        admin_count=view.admin_count,
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


@router.get(
    "/permissions",
    response_model=CustomResponse[list[PermissionOut]],
    summary="Every permission a role can have, grouped for the roles screen",
)
async def list_permissions(_: CurrentAdmin) -> CustomResponse[list[PermissionOut]]:
    return CustomResponse(
        message="OK", data=[PermissionOut.model_validate(info) for info in CATALOG]
    )


@router.get(
    "/roles",
    response_model=CustomResponse[list[RoleOut]],
    summary="All roles with their permissions and how many admins have each (Owner first)",
)
async def list_roles(_: TeamViewer, service: RoleServiceDep) -> CustomResponse[list[RoleOut]]:
    return CustomResponse(message="OK", data=[_out(view) for view in await service.list()])


@router.get(
    "/roles/{ref}", response_model=CustomResponse[RoleOut], summary="One role, by id or ROL- code"
)
async def get_role(ref: str, _: TeamViewer, service: RoleServiceDep) -> CustomResponse[RoleOut]:
    return CustomResponse(message="OK", data=_out(await service.view(await service.get(ref))))


@router.post(
    "/roles",
    status_code=status.HTTP_201_CREATED,
    response_model=CustomResponse[RoleOut],
    summary="Create a role. You can only give it permissions you have yourself",
)
async def create_role(
    body: RoleCreate, admin: RoleManager, service: RoleServiceDep
) -> CustomResponse[RoleOut]:
    return CustomResponse(message="Role created.", data=_out(await service.create(admin, body)))


@router.patch(
    "/roles/{ref}",
    response_model=CustomResponse[RoleOut],
    summary="Rename a role or change its permissions (not the Owner role)",
)
async def update_role(
    ref: str, body: RoleUpdate, admin: RoleManager, service: RoleServiceDep
) -> CustomResponse[RoleOut]:
    return CustomResponse(
        message="Role updated.", data=_out(await service.update(admin, ref, body))
    )


@router.delete(
    "/roles/{ref}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a role nobody has (not the Owner role)",
)
async def delete_role(ref: str, admin: RoleManager, service: RoleServiceDep) -> Response:
    await service.delete(admin, ref)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
