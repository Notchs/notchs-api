"""Venue app login and account: /venue/auth/*, /venue/me, /venue/me/email/*."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status

from app.clients.email import EmailClient, get_email_client
from app.clients.turnstile import TurnstileClient, get_turnstile_client
from app.common.dependencies import DBSession, RedisClient
from app.common.http import client_ip, user_agent
from app.common.schemas import CustomResponse
from app.modules.auth.cookies import set_session_cookies
from app.modules.auth.dependencies import CurrentVenueMember
from app.modules.auth.enums import SubjectType
from app.modules.auth.exceptions import CaptchaFailedException
from app.modules.auth.schemas import (
    ChangeEmailIn,
    ChangePasswordIn,
    EmailCodeIn,
    ForgotPasswordIn,
    LoginIn,
    SessionOut,
    SetPasswordIn,
    VenueMeOut,
    VenueSummaryOut,
)
from app.modules.auth.sessions import IssuedSession
from app.modules.auth.venue_auth import LoggedIn, VenueAuthService
from app.modules.venues.models import Venue, VenueMember

router = APIRouter(prefix="/venue", tags=["Venue auth"])


def get_venue_auth_service(session: DBSession, redis: RedisClient) -> VenueAuthService:
    return VenueAuthService(session, redis)


VenueAuthServiceDep = Annotated[VenueAuthService, Depends(get_venue_auth_service)]
EmailClientDep = Annotated[EmailClient, Depends(get_email_client)]
TurnstileClientDep = Annotated[TurnstileClient, Depends(get_turnstile_client)]


def _me(member: VenueMember, venue: Venue, session: IssuedSession | None = None) -> VenueMeOut:
    return VenueMeOut(
        id=member.id,
        name=member.name,
        email=member.email,
        role=member.role,
        venue=VenueSummaryOut.model_validate(venue),
        session=SessionOut(access_expires_at=session.access_expires_at) if session else None,
    )


def _set_cookies(response: Response, logged_in: LoggedIn) -> None:
    issued = logged_in.session
    set_session_cookies(
        response,
        SubjectType.VENUE_MEMBER,
        access_token=issued.access_token,
        access_expires_at=issued.access_expires_at,
        refresh_token=issued.refresh_token,
        refresh_expires_at=issued.refresh_expires_at,
    )


@router.post("/auth/login", response_model=CustomResponse[VenueMeOut], summary="Log in")
async def login(
    body: LoginIn, request: Request, response: Response, auth: VenueAuthServiceDep
) -> CustomResponse[VenueMeOut]:
    logged_in = await auth.login(
        body.email, body.password, ip=client_ip(request), user_agent=user_agent(request)
    )
    _set_cookies(response, logged_in)
    return CustomResponse(
        message="Logged in.", data=_me(logged_in.member, logged_in.venue, logged_in.session)
    )


@router.post(
    "/auth/set-password",
    response_model=CustomResponse[VenueMeOut],
    summary="Set a password from an emailed link (first time or reset) and log in",
)
async def set_password(
    body: SetPasswordIn, request: Request, response: Response, auth: VenueAuthServiceDep
) -> CustomResponse[VenueMeOut]:
    logged_in = await auth.set_password(
        body.token, body.password, ip=client_ip(request), user_agent=user_agent(request)
    )
    _set_cookies(response, logged_in)
    return CustomResponse(
        message="Password set.", data=_me(logged_in.member, logged_in.venue, logged_in.session)
    )


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
    auth: VenueAuthServiceDep,
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
        message="If this email belongs to a Notchs venue, a reset link is on its way."
    )


@router.get("/me", response_model=CustomResponse[VenueMeOut], summary="The logged-in venue member")
async def me(member: CurrentVenueMember, auth: VenueAuthServiceDep) -> CustomResponse[VenueMeOut]:
    return CustomResponse(message="OK", data=_me(member, await auth.venue_of(member)))


@router.post(
    "/auth/change-password",
    response_model=CustomResponse[VenueMeOut],
    summary="Change the password; this device stays logged in, other devices are logged out",
)
async def change_password(
    body: ChangePasswordIn,
    member: CurrentVenueMember,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    auth: VenueAuthServiceDep,
    email_client: EmailClientDep,
) -> CustomResponse[VenueMeOut]:
    logged_in, notice = await auth.change_password(
        member,
        body.current_password,
        body.new_password,
        ip=client_ip(request),
        user_agent=user_agent(request),
    )
    _set_cookies(response, logged_in)
    background.add_task(email_client.send, notice)
    return CustomResponse(
        message="Password changed. Other devices were logged out.",
        data=_me(logged_in.member, logged_in.venue, logged_in.session),
    )


@router.post(
    "/me/email/send-code",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CustomResponse[None],
    summary="Change email, step 1: check the password and email a code to the new address",
)
async def send_email_change_code(
    body: ChangeEmailIn,
    member: CurrentVenueMember,
    request: Request,
    background: BackgroundTasks,
    auth: VenueAuthServiceDep,
    email_client: EmailClientDep,
) -> CustomResponse[None]:
    message = await auth.request_email_change(
        member, body.new_email, body.current_password, ip=client_ip(request)
    )
    if message is not None:
        background.add_task(email_client.send, message)
    return CustomResponse(message="If this email can be used, a 6-digit code is on its way to it.")


@router.post(
    "/me/email/verify",
    response_model=CustomResponse[VenueMeOut],
    summary="Change email, step 2: confirm the code; the old address gets a notice",
)
async def verify_email_change(
    body: EmailCodeIn,
    member: CurrentVenueMember,
    background: BackgroundTasks,
    auth: VenueAuthServiceDep,
    email_client: EmailClientDep,
) -> CustomResponse[VenueMeOut]:
    notice = await auth.confirm_email_change(member, body.code)
    background.add_task(email_client.send, notice)
    return CustomResponse(
        message="Login email changed.", data=_me(member, await auth.venue_of(member))
    )
