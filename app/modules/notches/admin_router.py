"""`/admin/notches` (G1 proof review) and `/admin/fraud-signals` (G4). Reviewers can decide
too: proof review is their job."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.common.dependencies import DBSession, Pagination
from app.common.schemas import CustomResponse, PaginatedResponse
from app.modules.auth.dependencies import CurrentAdmin
from app.modules.notches.admin_schemas import (
    FraudSignalOut,
    ResolveSignalIn,
    ReviewDecisionIn,
    ReviewNotchDetailOut,
    ReviewNotchOut,
)
from app.modules.notches.admin_service import NotchReviewService
from app.modules.notches.models import NotchStatus, SignalStatus

router = APIRouter(prefix="/admin", tags=["Admin: proof review & fraud"])


def get_review_service(session: DBSession) -> NotchReviewService:
    return NotchReviewService(session)


ReviewDep = Annotated[NotchReviewService, Depends(get_review_service)]


@router.get(
    "/notches",
    response_model=CustomResponse[PaginatedResponse[ReviewNotchOut]],
    summary="The proof review queue (flagged, oldest first), or any Notchs by status",
)
async def notches(
    _: CurrentAdmin,
    service: ReviewDep,
    pagination: Pagination,
    status: NotchStatus | None = NotchStatus.FLAGGED,
    venue_id: uuid.UUID | None = None,
) -> CustomResponse[PaginatedResponse[ReviewNotchOut]]:
    items, total = await service.queue(pagination, status=status, venue_id=venue_id)
    return CustomResponse(
        message="OK", data=PaginatedResponse.build(items, total, pagination.page, pagination.size)
    )


@router.get(
    "/notches/{ref}",
    response_model=CustomResponse[ReviewNotchDetailOut],
    summary="One Notch (id or NCH- code), with others made with the same code",
)
async def notch(
    ref: str, _: CurrentAdmin, service: ReviewDep
) -> CustomResponse[ReviewNotchDetailOut]:
    return CustomResponse(message="OK", data=await service.get(ref))


@router.post(
    "/notches/{ref}/approve",
    response_model=CustomResponse[ReviewNotchOut],
    summary="Approve (A): the Notch is verified",
)
async def approve(
    ref: str, body: ReviewDecisionIn, admin: CurrentAdmin, service: ReviewDep
) -> CustomResponse[ReviewNotchOut]:
    return CustomResponse(
        message="Approved.", data=await service.decide(ref, admin, approve=True, note=body.note)
    )


@router.post(
    "/notches/{ref}/reject",
    response_model=CustomResponse[ReviewNotchOut],
    summary="Reject (R): the Notch stops counting everywhere",
)
async def reject(
    ref: str, body: ReviewDecisionIn, admin: CurrentAdmin, service: ReviewDep
) -> CustomResponse[ReviewNotchOut]:
    return CustomResponse(
        message="Rejected.", data=await service.decide(ref, admin, approve=False, note=body.note)
    )


@router.get(
    "/fraud-signals",
    response_model=CustomResponse[PaginatedResponse[FraudSignalOut]],
    summary="Patterns across scans (G4), newest first",
)
async def fraud_signals(
    _: CurrentAdmin,
    service: ReviewDep,
    pagination: Pagination,
    status: SignalStatus | None = SignalStatus.OPEN,
) -> CustomResponse[PaginatedResponse[FraudSignalOut]]:
    items, total = await service.signals(pagination, status=status)
    return CustomResponse(
        message="OK", data=PaginatedResponse.build(items, total, pagination.page, pagination.size)
    )


@router.post(
    "/fraud-signals/{signal_id}/resolve",
    response_model=CustomResponse[FraudSignalOut],
    summary="Mark a signal dismissed (nothing wrong) or actioned",
)
async def resolve(
    signal_id: uuid.UUID, body: ResolveSignalIn, admin: CurrentAdmin, service: ReviewDep
) -> CustomResponse[FraudSignalOut]:
    return CustomResponse(
        message="Saved.", data=await service.resolve(signal_id, admin, body.status, body.note)
    )
