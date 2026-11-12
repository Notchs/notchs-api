"""Bill-photo claims (service.py).

/me/claims      the logged-in customer: find the place, start a claim, see their claims
/admin/claims   admins and reviewers: the queue, the photo, approve or reject
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.clients.whatsapp import WhatsAppClient, get_whatsapp_client
from app.common.dependencies import DBSession, Pagination, RedisClient
from app.common.schemas import CustomResponse, PaginatedResponse, PublicCode
from app.modules.auth.dependencies import CurrentAdmin, CurrentUser
from app.modules.claims.models import ClaimStatus
from app.modules.claims.schemas import (
    AdminClaimOut,
    ClaimDecisionOut,
    ClaimRejectIn,
    ClaimStartIn,
    ClaimStartOut,
    ClaimVenueOut,
    MyClaimOut,
)
from app.modules.claims.service import ClaimService

router = APIRouter(prefix="/me/claims", tags=["Passport"])
admin_router = APIRouter(prefix="/admin/claims", tags=["Admin: bill-photo claims"])


def get_claim_service(session: DBSession, redis: RedisClient) -> ClaimService:
    return ClaimService(session, redis)


ClaimDep = Annotated[ClaimService, Depends(get_claim_service)]
WhatsAppClientDep = Annotated[WhatsAppClient, Depends(get_whatsapp_client)]


@router.get(
    "/venues",
    response_model=CustomResponse[list[ClaimVenueOut]],
    summary="Live places by name, to claim a Notch at",
)
async def venues(
    _: CurrentUser,
    claims: ClaimDep,
    q: Annotated[str, Query(min_length=2, max_length=60)],
) -> CustomResponse[list[ClaimVenueOut]]:
    return CustomResponse(message="OK", data=await claims.search_venues(q.strip()))


@router.post(
    "",
    response_model=CustomResponse[ClaimStartOut],
    summary="Start a claim: then send the code and the bill photo on WhatsApp, today",
)
async def start(
    body: ClaimStartIn, user: CurrentUser, claims: ClaimDep
) -> CustomResponse[ClaimStartOut]:
    return CustomResponse(
        message="Send the code, then a photo of your bill, on WhatsApp today.",
        data=await claims.start(user, body.venue_code),
    )


@router.get("", response_model=CustomResponse[list[MyClaimOut]], summary="My claims, newest first")
async def mine(user: CurrentUser, claims: ClaimDep) -> CustomResponse[list[MyClaimOut]]:
    return CustomResponse(message="OK", data=await claims.mine(user))


@admin_router.get(
    "",
    response_model=CustomResponse[PaginatedResponse[AdminClaimOut]],
    summary="The claims queue, oldest first. Defaults to claims waiting for review",
)
async def list_claims(
    _: CurrentAdmin,
    claims: ClaimDep,
    pagination: Pagination,
    status: ClaimStatus | None = ClaimStatus.IN_REVIEW,
) -> CustomResponse[PaginatedResponse[AdminClaimOut]]:
    items, total = await claims.list(status, pagination)
    return CustomResponse(
        message="OK",
        data=PaginatedResponse.build(items, total, pagination.page, pagination.size),
    )


@admin_router.get(
    "/{claim_code}", response_model=CustomResponse[AdminClaimOut], summary="One claim"
)
async def get_claim(
    claim_code: PublicCode, _: CurrentAdmin, claims: ClaimDep
) -> CustomResponse[AdminClaimOut]:
    return CustomResponse(message="OK", data=await claims.get(claim_code))


@admin_router.get(
    "/{claim_code}/photo",
    response_class=Response,
    summary="The bill photo, fetched from WhatsApp now (it keeps photos about 30 days)",
)
async def photo(claim_code: PublicCode, _: CurrentAdmin, claims: ClaimDep) -> Response:
    media = await claims.photo(claim_code)
    return Response(
        content=media.content,
        media_type=media.mime_type,
        headers={
            # A customer's bill: never kept by browsers or caches.
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )


@admin_router.post(
    "/{claim_code}/approve",
    response_model=CustomResponse[ClaimDecisionOut],
    summary="Count the visit: a Notch with points, marked verified",
)
async def approve(
    claim_code: PublicCode, admin: CurrentAdmin, claims: ClaimDep, client: WhatsAppClientDep
) -> CustomResponse[ClaimDecisionOut]:
    decision = await claims.approve(claim_code, admin, client)
    return CustomResponse(message="Approved. The Notch is added.", data=decision)


@admin_router.post(
    "/{claim_code}/reject",
    response_model=CustomResponse[ClaimDecisionOut],
    summary="Turn the claim down; the reason is shown to the customer",
)
async def reject(
    claim_code: PublicCode,
    body: ClaimRejectIn,
    admin: CurrentAdmin,
    claims: ClaimDep,
    client: WhatsAppClientDep,
) -> CustomResponse[ClaimDecisionOut]:
    decision = await claims.reject(claim_code, admin, body.reason, client, code=body.code)
    return CustomResponse(message="Rejected.", data=decision)
