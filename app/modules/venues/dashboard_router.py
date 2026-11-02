"""The venue app's dashboard (E5-E14), for the logged-in venue member (`nx_venue_*`)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status

from app.clients.email import EmailClient, get_email_client
from app.common.dependencies import DBSession, Pagination
from app.common.schemas import CustomResponse, PaginatedResponse
from app.modules.auth.dependencies import CurrentVenueMember
from app.modules.locations.time import month_from_string
from app.modules.venues.dashboard import DashboardService
from app.modules.venues.dashboard_schemas import (
    BillingOut,
    CertificateOut,
    CloseRequestIn,
    ContactOut,
    ContactUpdate,
    CustomersOut,
    DayOut,
    OwnerHomeOut,
    Range,
    VenueCardOut,
    VisitOut,
)
from app.modules.venues.exceptions import RangeInvalidException
from app.modules.venues.schemas import VenuePageOut, VenueProfileUpdate

router = APIRouter(prefix="/venue", tags=["Venue dashboard"])


def get_dashboard(session: DBSession) -> DashboardService:
    return DashboardService(session)


DashboardDep = Annotated[DashboardService, Depends(get_dashboard)]
EmailClientDep = Annotated[EmailClient, Depends(get_email_client)]


@router.get("/home", response_model=CustomResponse[OwnerHomeOut], summary="Owner home (E5)")
async def home(
    member: CurrentVenueMember, dashboard: DashboardDep, range: Range = "7d"
) -> CustomResponse[OwnerHomeOut]:
    return CustomResponse(message="OK", data=await dashboard.home(member, range))


@router.get(
    "/daily",
    response_model=CustomResponse[list[DayOut]],
    summary="Day by day: visits, customers, new (up to 92 days)",
)
async def daily(
    member: CurrentVenueMember,
    dashboard: DashboardDep,
    start: Annotated[date, Query(alias="from")],
    end: Annotated[date, Query(alias="to")],
) -> CustomResponse[list[DayOut]]:
    return CustomResponse(message="OK", data=await dashboard.daily(member, start, end))


@router.get("/customers", response_model=CustomResponse[CustomersOut], summary="Customers (E6)")
async def customers(
    member: CurrentVenueMember, dashboard: DashboardDep, range: Range = "7d"
) -> CustomResponse[CustomersOut]:
    return CustomResponse(message="OK", data=await dashboard.customers(member, range))


@router.get(
    "/visits",
    response_model=CustomResponse[PaginatedResponse[VisitOut]],
    summary="Visits on one day, newest first: time and '6th visit' only (E6)",
)
async def visits(
    member: CurrentVenueMember,
    dashboard: DashboardDep,
    pagination: Pagination,
    day: Annotated[date | None, Query(alias="date")] = None,
) -> CustomResponse[PaginatedResponse[VisitOut]]:
    items, total = await dashboard.visits(member, day, pagination)
    return CustomResponse(
        message="OK",
        data=PaginatedResponse.build(items, total, pagination.page, pagination.size),
    )


@router.get(
    "/visits.csv",
    summary="Export visits as CSV, up to a year (E6). No customer details",
    response_class=Response,
)
async def export_visits(
    member: CurrentVenueMember,
    dashboard: DashboardDep,
    start: Annotated[date, Query(alias="from")],
    end: Annotated[date, Query(alias="to")],
) -> Response:
    content = await dashboard.export_csv(member, start, end)
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="notchs-visits-{start}-{end}.csv"'},
    )


@router.get(
    "/profile",
    response_model=CustomResponse[VenuePageOut],
    summary="The venue page as customers see it (E8 preview)",
)
async def profile(
    member: CurrentVenueMember, dashboard: DashboardDep
) -> CustomResponse[VenuePageOut]:
    return CustomResponse(message="OK", data=await dashboard.profile(member))


@router.patch(
    "/profile",
    response_model=CustomResponse[VenuePageOut],
    summary="Edit description, hours, tags, address (E8)",
)
async def update_profile(
    body: VenueProfileUpdate, member: CurrentVenueMember, dashboard: DashboardDep
) -> CustomResponse[VenuePageOut]:
    return CustomResponse(message="Saved.", data=await dashboard.update_profile(member, body))


@router.patch(
    "/me/contact",
    response_model=CustomResponse[ContactOut],
    summary="The owner's name and WhatsApp number (E11)",
)
async def update_contact(
    body: ContactUpdate, member: CurrentVenueMember, dashboard: DashboardDep
) -> CustomResponse[ContactOut]:
    return CustomResponse(message="Saved.", data=await dashboard.update_contact(member, body))


@router.get(
    "/cards",
    response_model=CustomResponse[list[VenueCardOut]],
    summary="Monthly venue cards, newest first (E7)",
)
async def cards(
    member: CurrentVenueMember, dashboard: DashboardDep
) -> CustomResponse[list[VenueCardOut]]:
    return CustomResponse(message="OK", data=await dashboard.cards(member))


@router.get(
    "/cards/{month}",
    response_model=CustomResponse[VenueCardOut],
    summary="One month's venue card, e.g. 2026-10 (E7)",
)
async def card(
    month: str, member: CurrentVenueMember, dashboard: DashboardDep
) -> CustomResponse[VenueCardOut]:
    try:
        period = month_from_string(month)
    except ValueError as exc:
        raise RangeInvalidException("Use a month like 2026-10.") from exc
    return CustomResponse(message="OK", data=await dashboard.card(member, period))


@router.get(
    "/certificate",
    response_model=CustomResponse[CertificateOut],
    summary="Founding Venue certificate (E14); 404 for other venues",
)
async def certificate(
    member: CurrentVenueMember, dashboard: DashboardDep
) -> CustomResponse[CertificateOut]:
    return CustomResponse(message="OK", data=await dashboard.certificate(member))


@router.get("/billing", response_model=CustomResponse[BillingOut], summary="Billing (E10)")
async def billing(
    member: CurrentVenueMember, dashboard: DashboardDep
) -> CustomResponse[BillingOut]:
    return CustomResponse(message="OK", data=await dashboard.billing(member))


@router.post(
    "/close-request",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CustomResponse[None],
    summary="Ask the Notchs team to close the venue (E11 'Delete venue')",
)
async def close_request(
    body: CloseRequestIn,
    member: CurrentVenueMember,
    dashboard: DashboardDep,
    background: BackgroundTasks,
    email_client: EmailClientDep,
) -> CustomResponse[None]:
    notice = await dashboard.close_request(member, body.reason)
    if notice is not None:
        background.add_task(email_client.send, notice)
    return CustomResponse(message="We'll be in touch within a day to close your venue.")
