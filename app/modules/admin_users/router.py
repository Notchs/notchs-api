"""`/admin/users`: admin Settings, the team's admin accounts. Role `admin` only."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends

from app.common.dependencies import DBSession, RedisClient
from app.common.schemas import CustomResponse, PublicCode
from app.jobs.email import EmailQueue, get_email_queue
from app.modules.admin_users.schemas import AdminUserIn, AdminUserOut, AdminUserUpdate
from app.modules.admin_users.service import AdminUserService
from app.modules.auth.admin_auth import AdminAuthService
from app.modules.auth.dependencies import CurrentFullAdmin

router = APIRouter(prefix="/admin/users", tags=["Admin: settings"])


def get_admin_user_service(session: DBSession, redis: RedisClient) -> AdminUserService:
    return AdminUserService(session, AdminAuthService(session, redis))


ServiceDep = Annotated[AdminUserService, Depends(get_admin_user_service)]
EmailQueueDep = Annotated[EmailQueue, Depends(get_email_queue)]


@router.get("", response_model=CustomResponse[list[AdminUserOut]], summary="Every admin account")
async def list_admins(
    _: CurrentFullAdmin, admins: ServiceDep
) -> CustomResponse[list[AdminUserOut]]:
    return CustomResponse(message="OK", data=await admins.list())


@router.post(
    "",
    response_model=CustomResponse[AdminUserOut],
    summary="Add an admin or reviewer; they get an email link to set their password",
)
async def invite(
    body: AdminUserIn,
    actor: CurrentFullAdmin,
    admins: ServiceDep,
    background: BackgroundTasks,
    emails: EmailQueueDep,
) -> CustomResponse[AdminUserOut]:
    admin, message = await admins.invite(actor, body)
    background.add_task(emails.enqueue, message)
    return CustomResponse(
        message=f"Added. {admin.email} will get a link to set a password.", data=admin
    )


@router.patch(
    "/{code}",
    response_model=CustomResponse[AdminUserOut],
    summary="Change an admin's role, or turn them off (logs them out) or back on",
)
async def update(
    code: PublicCode, body: AdminUserUpdate, actor: CurrentFullAdmin, admins: ServiceDep
) -> CustomResponse[AdminUserOut]:
    return CustomResponse(message="Saved.", data=await admins.update(actor, code, body))


@router.post(
    "/{code}/link",
    response_model=CustomResponse[None],
    summary="Email a new set-password link (older links stop working)",
)
async def new_link(
    code: PublicCode,
    actor: CurrentFullAdmin,
    admins: ServiceDep,
    background: BackgroundTasks,
    emails: EmailQueueDep,
) -> CustomResponse[None]:
    background.add_task(emails.enqueue, await admins.new_link(actor, code))
    return CustomResponse(message="Sent a new link.")
