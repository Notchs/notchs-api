"""`/admin/venues` (G2): every venue, its status, and its numbers. Admins and reviewers can
look; only admins (role `admin`) can change anything. New sign-ups are approved in
`/admin/venue-applications`."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.common.dependencies import DBSession, Pagination
from app.common.schemas import CustomResponse, PaginatedResponse
from app.modules.auth.dependencies import CurrentAdmin, CurrentFullAdmin
from app.modules.venues.admin_schemas import AdminVenueOut, AdminVenueRowOut
from app.modules.venues.admin_service import VenueAdminService
from app.modules.venues.models import VenueStatus
from app.modules.venues.schemas import VenueStatusUpdate, VenueUpdate

router = APIRouter(prefix="/admin/venues", tags=["Admin: venues"])


def get_venue_admin(session: DBSession) -> VenueAdminService:
    return VenueAdminService(session)


VenueAdminDep = Annotated[VenueAdminService, Depends(get_venue_admin)]


@router.get(
    "",
    response_model=CustomResponse[PaginatedResponse[AdminVenueRowOut]],
    summary="Venues, by status or name (G2)",
)
async def list_venues(
    _: CurrentAdmin,
    service: VenueAdminDep,
    pagination: Pagination,
    status: VenueStatus | None = None,
    q: str | None = None,
) -> CustomResponse[PaginatedResponse[AdminVenueRowOut]]:
    items, total = await service.list(pagination, status=status, q=q)
    return CustomResponse(
        message="OK", data=PaginatedResponse.build(items, total, pagination.page, pagination.size)
    )


@router.get(
    "/{ref}", response_model=CustomResponse[AdminVenueOut], summary="One venue (id or VEN- code)"
)
async def get_venue(
    ref: str, _: CurrentAdmin, service: VenueAdminDep
) -> CustomResponse[AdminVenueOut]:
    return CustomResponse(message="OK", data=await service.get(ref))


@router.patch(
    "/{ref}",
    response_model=CustomResponse[AdminVenueOut],
    summary="Edit any venue field: name, category, area, location, fee, free months...",
)
async def update_venue(
    ref: str, body: VenueUpdate, admin: CurrentFullAdmin, service: VenueAdminDep
) -> CustomResponse[AdminVenueOut]:
    return CustomResponse(message="Saved.", data=await service.update(ref, body, admin))


@router.post(
    "/{ref}/status",
    response_model=CustomResponse[AdminVenueOut],
    summary="Pause, resume or close a venue. A paused venue cannot notch",
)
async def set_status(
    ref: str, body: VenueStatusUpdate, admin: CurrentFullAdmin, service: VenueAdminDep
) -> CustomResponse[AdminVenueOut]:
    return CustomResponse(message="Saved.", data=await service.set_status(ref, body.status, admin))
