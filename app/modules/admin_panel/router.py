"""`/admin/*` for the Notchs team (admin login, `nx_admin_*` cookies). Viewing and removing
Notchs needs any admin; status changes need the `admin` role."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.common.dependencies import DBSession, Pagination, RedisClient
from app.common.schemas import CustomResponse, PaginatedResponse
from app.modules.admin_panel.schemas import (
    AdminCustomerRow,
    AdminNotchRow,
    AdminVenueRow,
    CustomerStatusIn,
    OverviewOut,
    RevokeIn,
    VenueStatusIn,
)
from app.modules.admin_panel.service import AdminPanelService
from app.modules.auth.dependencies import CurrentAdmin, CurrentFullAdmin
from app.modules.scans.models import VisitStatus
from app.modules.users.models import UserStatus
from app.modules.venues.models import VenueStatus

router = APIRouter(prefix="/admin", tags=["Admin panel"])

VenueCode = Annotated[str, Path(pattern=r"^VEN-[0-9A-Z]{8}$")]
Search = Annotated[str | None, Query(min_length=1, max_length=60)]


def get_admin_panel_service(session: DBSession, redis: RedisClient) -> AdminPanelService:
    return AdminPanelService(session, redis)


PanelDep = Annotated[AdminPanelService, Depends(get_admin_panel_service)]


@router.get("/overview", response_model=CustomResponse[OverviewOut], summary="Real totals")
async def overview(_: CurrentAdmin, panel: PanelDep) -> CustomResponse[OverviewOut]:
    return CustomResponse(message="OK", data=await panel.overview())


@router.get(
    "/venues",
    response_model=CustomResponse[PaginatedResponse[AdminVenueRow]],
    summary="Venues, newest first, with their counter phone and Notch counts",
)
async def venues(
    _: CurrentAdmin,
    panel: PanelDep,
    pagination: Pagination,
    status: VenueStatus | None = None,
    q: Search = None,
) -> CustomResponse[PaginatedResponse[AdminVenueRow]]:
    return CustomResponse(message="OK", data=await panel.venues(pagination, status=status, q=q))


@router.patch(
    "/venues/{venue_code}/status",
    response_model=CustomResponse[None],
    summary="Pause, resume or close a venue",
)
async def set_venue_status(
    venue_code: VenueCode, body: VenueStatusIn, admin: CurrentFullAdmin, panel: PanelDep
) -> CustomResponse[None]:
    await panel.set_venue_status(admin, venue_code, body.status)
    return CustomResponse(message="Saved.")


@router.get(
    "/notchs",
    response_model=CustomResponse[PaginatedResponse[AdminNotchRow]],
    summary="Every Notch, newest first",
)
async def notchs(
    _: CurrentAdmin,
    panel: PanelDep,
    pagination: Pagination,
    venue_code: Annotated[str | None, Query(pattern=r"^VEN-[0-9A-Z]{8}$")] = None,
    status: VisitStatus | None = None,
) -> CustomResponse[PaginatedResponse[AdminNotchRow]]:
    return CustomResponse(
        message="OK", data=await panel.notchs(pagination, venue_code=venue_code, status=status)
    )


@router.post(
    "/venues/{venue_code}/notchs/{notch_no}/revoke",
    response_model=CustomResponse[AdminNotchRow],
    summary="Remove a fraudulent Notch (it stops counting everywhere)",
)
async def revoke(
    venue_code: VenueCode,
    notch_no: Annotated[int, Path(ge=1)],
    body: RevokeIn,
    admin: CurrentAdmin,
    panel: PanelDep,
) -> CustomResponse[AdminNotchRow]:
    return CustomResponse(
        message="Notch removed.",
        data=await panel.revoke(admin, venue_code, notch_no, body.reason),
    )


@router.get(
    "/customers",
    response_model=CustomResponse[PaginatedResponse[AdminCustomerRow]],
    summary="Customers by handle, newest first (only the last 4 digits of the phone number)",
)
async def customers(
    _: CurrentAdmin,
    panel: PanelDep,
    pagination: Pagination,
    status: UserStatus | None = None,
    q: Search = None,
) -> CustomResponse[PaginatedResponse[AdminCustomerRow]]:
    return CustomResponse(message="OK", data=await panel.customers(pagination, status=status, q=q))


@router.patch(
    "/customers/{handle}/status",
    response_model=CustomResponse[None],
    summary="Block or unblock a customer",
)
async def set_customer_status(
    handle: Annotated[str, Path(pattern=r"^[a-z0-9._]{3,30}$")],
    body: CustomerStatusIn,
    admin: CurrentFullAdmin,
    panel: PanelDep,
) -> CustomResponse[None]:
    await panel.set_customer_status(admin, handle, body.status)
    return CustomResponse(message="Saved.")
