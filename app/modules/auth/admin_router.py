"""Admin panel login: /admin/auth/* and /admin/me. Mirrors the venue app's endpoints."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status

from app.clients.email import EmailClient, get_email_client
from app.clients.turnstile import TurnstileClient, get_turnstile_client
from app.common.dependencies import DBSession, RedisClient
from app.common.http import client_ip, user_agent
from app.common.schemas import CustomResponse
from app.modules.admin_users.models import AdminUser
from app.modules.auth.admin_auth import AdminAuthService
from app.modules.auth.cookies import set_session_cookies
from app.modules.auth.dependencies import CurrentAdmin
from app.modules.auth.enums import SubjectType
from app.modules.auth.exceptions import CaptchaFailedException
from app.modules.auth.password_auth import PasswordLogin
from app.modules.auth.schemas import (
    AdminMeOut,
    ChangePasswordIn,
    ForgotPasswordIn,
    LoginIn,
    SessionOut,
    SetPasswordIn,
)

router = APIRouter(prefix="/admin", tags=["Admin auth"])


def get_admin_auth_service(session: DBSession, redis: RedisClient) -> AdminAuthService:
    return AdminAuthService(session, redis)


AdminAuthServiceDep = Annotated[AdminAuthService, Depends(get_admin_auth_service)]
EmailClientDep = Annotated[EmailClient, Depends(get_email_client)]
TurnstileClientDep = Annotated[TurnstileClient, Depends(get_turnstile_client)]


def _me(admin: AdminUser, login: PasswordLogin[AdminUser] | None = None) -> AdminMeOut:
    return AdminMeOut(
        id=admin.id,
        admin_user_code=admin.admin_user_code,
        name=admin.name,
        email=admin.email,
        role=admin.role,
        session=SessionOut(access_expires_at=login.session.access_expires_at) if login else None,
    )


def _logged_in(response: Response, login: PasswordLogin[AdminUser]) -> AdminMeOut:
    issued = login.session
    set_session_cookies(
        response,
        SubjectType.ADMIN,
        access_token=issued.access_token,
        access_expires_at=issued.access_expires_at,
        refresh_token=issued.refresh_token,
        refresh_expires_at=issued.refresh_expires_at,
    )
    return _me(login.account, login)


@router.post("/auth/login", response_model=CustomResponse[AdminMeOut], summary="Log in")
async def login(
    body: LoginIn, request: Request, response: Response, auth: AdminAuthServiceDep
) -> CustomResponse[AdminMeOut]:
    login = await auth.login(
        body.email, body.password, ip=client_ip(request), user_agent=user_agent(request)
    )
    return CustomResponse(message="Logged in.", data=_logged_in(response, login))


@router.post(
    "/auth/set-password",
    response_model=CustomResponse[AdminMeOut],
    summary="Set a password from an emailed link (invite or reset) and log in",
)
async def set_password(
    body: SetPasswordIn, request: Request, response: Response, auth: AdminAuthServiceDep
) -> CustomResponse[AdminMeOut]:
    login = await auth.set_password(
        body.token, body.password, ip=client_ip(request), user_agent=user_agent(request)
    )
    return CustomResponse(message="Password set.", data=_logged_in(response, login))


@router.post(
    "/auth/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CustomResponse[None],
    summary="Email a password reset link (same answer whether or not the email is registered)",
)
async def forgot_password(
    body: ForgotPasswordIn,
    request: Request,
    background: BackgroundTasks,
    auth: AdminAuthServiceDep,
    email_client: EmailClientDep,
    turnstile: TurnstileClientDep,
) -> CustomResponse[None]:
    ip = client_ip(request)
    if not await turnstile.verify(body.turnstile_token, ip=ip):
        raise CaptchaFailedException()
    message = await auth.forgot_password(body.email, ip=ip)
    if message is not None:
        background.add_task(email_client.send, message)
    return CustomResponse(
        message="If this email belongs to a Notchs admin, a reset link is on its way."
    )


@router.get("/me", response_model=CustomResponse[AdminMeOut], summary="The logged-in admin")
async def me(admin: CurrentAdmin) -> CustomResponse[AdminMeOut]:
    return CustomResponse(message="OK", data=_me(admin))


@router.post(
    "/auth/change-password",
    response_model=CustomResponse[AdminMeOut],
    summary="Change the password; this device stays logged in, other devices are logged out",
)
async def change_password(
    body: ChangePasswordIn,
    admin: CurrentAdmin,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    auth: AdminAuthServiceDep,
    email_client: EmailClientDep,
) -> CustomResponse[AdminMeOut]:
    login, notice = await auth.change_password(
        admin,
        body.current_password,
        body.new_password,
        ip=client_ip(request),
        user_agent=user_agent(request),
    )
    background.add_task(email_client.send, notice)
    return CustomResponse(
        message="Password changed. Other devices were logged out.",
        data=_logged_in(response, login),
    )
