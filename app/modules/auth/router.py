from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.common.dependencies import DBSession, RedisClient
from app.common.http import client_ip, user_agent
from app.common.schemas import CustomResponse
from app.modules.auth.cookies import (
    AUDIENCE_SUBJECT,
    Audience,
    clear_session_cookies,
    refresh_cookie_name,
    set_session_cookies,
)
from app.modules.auth.customer_auth import CustomerAuthService
from app.modules.auth.dependencies import SessionServiceDep
from app.modules.auth.enums import SubjectType
from app.modules.auth.exceptions import NotAuthenticatedException
from app.modules.auth.schemas import ConsumeLinkIn, CustomerBriefOut, SessionOut

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post(
    "/{audience}/refresh",
    response_model=CustomResponse[SessionOut],
    summary="Swap the refresh cookie for new session cookies",
)
async def refresh(
    audience: Audience, request: Request, response: Response, sessions: SessionServiceDep
) -> CustomResponse[SessionOut]:
    subject_type = AUDIENCE_SUBJECT[audience]
    raw = request.cookies.get(refresh_cookie_name(subject_type))
    if not raw:
        raise NotAuthenticatedException()

    issued = await sessions.refresh(
        raw, subject_type, ip=client_ip(request), user_agent=user_agent(request)
    )
    set_session_cookies(
        response,
        subject_type,
        access_token=issued.access_token,
        access_expires_at=issued.access_expires_at,
        refresh_token=issued.refresh_token,
        refresh_expires_at=issued.refresh_expires_at,
    )
    return CustomResponse(
        message="Session refreshed.",
        data=SessionOut(access_expires_at=issued.access_expires_at),
    )


@router.post(
    "/{audience}/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End this session and clear its cookies",
)
async def logout(audience: Audience, request: Request, sessions: SessionServiceDep) -> Response:
    subject_type = AUDIENCE_SUBJECT[audience]
    raw = request.cookies.get(refresh_cookie_name(subject_type))
    if raw:
        await sessions.end(raw, subject_type)
    # Always succeeds and clears the cookies, even if the session was already gone.
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookies(response, subject_type)
    return response


def get_customer_auth_service(session: DBSession, redis: RedisClient) -> CustomerAuthService:
    return CustomerAuthService(session, redis)


CustomerAuthServiceDep = Annotated[CustomerAuthService, Depends(get_customer_auth_service)]


@router.post(
    "/customer/consume",
    response_model=CustomResponse[CustomerBriefOut],
    summary="Log a customer in with the single-use link the WhatsApp bot sent",
)
async def consume_customer_link(
    body: ConsumeLinkIn, request: Request, response: Response, auth: CustomerAuthServiceDep
) -> CustomResponse[CustomerBriefOut]:
    login = await auth.consume(body.token, ip=client_ip(request), user_agent=user_agent(request))
    issued = login.session
    set_session_cookies(
        response,
        SubjectType.USER,
        access_token=issued.access_token,
        access_expires_at=issued.access_expires_at,
        refresh_token=issued.refresh_token,
        refresh_expires_at=issued.refresh_expires_at,
    )
    user = login.user
    return CustomResponse(
        message="Logged in.",
        data=CustomerBriefOut(
            id=user.id,
            name=user.name,
            handle=user.handle,
            is_public=user.is_public,
            session=SessionOut(access_expires_at=issued.access_expires_at),
        ),
    )
