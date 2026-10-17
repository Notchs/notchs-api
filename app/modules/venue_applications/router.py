"""`/venue-applications`: the public "apply as a venue" form (docs/modules/auth.md §2)."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status

from app.clients.email import EmailClient, get_email_client
from app.clients.turnstile import TurnstileClient, get_turnstile_client
from app.common.dependencies import DBSession, RedisClient
from app.common.http import client_ip
from app.common.schemas import CustomResponse
from app.modules.auth.exceptions import CaptchaFailedException
from app.modules.auth.otp import VERIFICATION_TOKEN_TTL_SECONDS
from app.modules.venue_applications.schemas import (
    ApplicationCreate,
    ApplicationCreated,
    EmailVerifiedOut,
    SendCodeIn,
    VerifyCodeIn,
)
from app.modules.venue_applications.service import VenueApplicationService

router = APIRouter(prefix="/venue-applications", tags=["Venue applications"])


def get_application_service(session: DBSession, redis: RedisClient) -> VenueApplicationService:
    return VenueApplicationService(session, redis)


ApplicationServiceDep = Annotated[VenueApplicationService, Depends(get_application_service)]
EmailClientDep = Annotated[EmailClient, Depends(get_email_client)]
TurnstileClientDep = Annotated[TurnstileClient, Depends(get_turnstile_client)]


@router.post(
    "/email/send-code",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CustomResponse[None],
    summary="Email a 6-digit code to verify the applicant's address",
)
async def send_code(
    body: SendCodeIn,
    request: Request,
    background: BackgroundTasks,
    service: ApplicationServiceDep,
    email_client: EmailClientDep,
    turnstile: TurnstileClientDep,
) -> CustomResponse[None]:
    ip = client_ip(request)
    if not await turnstile.verify(body.turnstile_token, ip=ip):
        raise CaptchaFailedException()
    message = await service.send_code(body.email, ip=ip)
    background.add_task(email_client.send, message)
    return CustomResponse(message="We sent a 6-digit code to your email.")


@router.post(
    "/email/verify",
    response_model=CustomResponse[EmailVerifiedOut],
    summary="Check the code; returns the token the form is submitted with",
)
async def verify_code(
    body: VerifyCodeIn, service: ApplicationServiceDep
) -> CustomResponse[EmailVerifiedOut]:
    token = await service.verify_code(body.email, body.code)
    return CustomResponse(
        message="Email verified.",
        data=EmailVerifiedOut(
            verification_token=token, expires_in_seconds=VERIFICATION_TOKEN_TTL_SECONDS
        ),
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=CustomResponse[ApplicationCreated],
    summary="Submit the application",
)
async def submit(
    body: ApplicationCreate,
    request: Request,
    background: BackgroundTasks,
    service: ApplicationServiceDep,
    email_client: EmailClientDep,
) -> CustomResponse[ApplicationCreated]:
    application, notice = await service.submit(body, ip=client_ip(request))
    if notice is not None:
        background.add_task(email_client.send, notice)
    return CustomResponse(
        message="Application received. We'll email you once it's reviewed.",
        data=ApplicationCreated(application_code=application.application_code),
    )
