"""`/admin/venue-applications`: the review queue. Viewing needs `applications.view`; approving
and rejecting need `applications.review`."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends

from app.clients.email import EmailClient, get_email_client
from app.common.dependencies import DBSession, Pagination, RedisClient
from app.common.schemas import CustomResponse, PaginatedResponse
from app.modules.admin_users.models import AdminUser
from app.modules.admin_users.permissions import Permission
from app.modules.auth.dependencies import require_permission
from app.modules.venue_applications.admin_service import ApplicationReviewService
from app.modules.venue_applications.models import ApplicationStatus
from app.modules.venue_applications.schemas import (
    ApplicationOut,
    ApprovedOut,
    ApproveIn,
    RejectIn,
)

router = APIRouter(prefix="/admin/venue-applications", tags=["Admin: venue applications"])


def get_review_service(session: DBSession, redis: RedisClient) -> ApplicationReviewService:
    return ApplicationReviewService(session, redis)


ReviewServiceDep = Annotated[ApplicationReviewService, Depends(get_review_service)]
EmailClientDep = Annotated[EmailClient, Depends(get_email_client)]
QueueViewer = Annotated[AdminUser, Depends(require_permission(Permission.APPLICATIONS_VIEW))]
Reviewer = Annotated[AdminUser, Depends(require_permission(Permission.APPLICATIONS_REVIEW))]


@router.get(
    "",
    response_model=CustomResponse[PaginatedResponse[ApplicationOut]],
    summary="The review queue, oldest first. Defaults to pending applications",
)
async def list_applications(
    _: QueueViewer,
    service: ReviewServiceDep,
    pagination: Pagination,
    status: ApplicationStatus | None = ApplicationStatus.PENDING,
) -> CustomResponse[PaginatedResponse[ApplicationOut]]:
    items, total = await service.list(pagination, status=status)
    page = PaginatedResponse.build(
        [ApplicationOut.model_validate(item) for item in items],
        total,
        pagination.page,
        pagination.size,
    )
    return CustomResponse(message="OK", data=page)


@router.get(
    "/{ref}",
    response_model=CustomResponse[ApplicationOut],
    summary="One application, by id or APP- code",
)
async def get_application(
    ref: str, _: QueueViewer, service: ReviewServiceDep
) -> CustomResponse[ApplicationOut]:
    return CustomResponse(message="OK", data=ApplicationOut.model_validate(await service.get(ref)))


@router.post(
    "/{ref}/approve",
    response_model=CustomResponse[ApprovedOut],
    summary="Create the venue and its owner login, then email the set-password link",
)
async def approve(
    ref: str,
    body: ApproveIn,
    admin: Reviewer,
    service: ReviewServiceDep,
    background: BackgroundTasks,
    email_client: EmailClientDep,
) -> CustomResponse[ApprovedOut]:
    application, venue, member, welcome = await service.approve(ref, admin, body)
    background.add_task(email_client.send, welcome)
    return CustomResponse(
        message=f"Approved. {member.email} will get a link to set their password.",
        data=ApprovedOut(
            application=ApplicationOut.model_validate(application),
            venue_id=venue.id,
            venue_code=venue.venue_code,
            venue_slug=venue.slug,
            owner_email=member.email,
        ),
    )


@router.post(
    "/{ref}/reject",
    response_model=CustomResponse[ApplicationOut],
    summary="Reject, optionally emailing the applicant",
)
async def reject(
    ref: str,
    body: RejectIn,
    admin: Reviewer,
    service: ReviewServiceDep,
    background: BackgroundTasks,
    email_client: EmailClientDep,
) -> CustomResponse[ApplicationOut]:
    application, notice = await service.reject(ref, admin, body)
    if notice is not None:
        background.add_task(email_client.send, notice)
    return CustomResponse(message="Rejected.", data=ApplicationOut.model_validate(application))
