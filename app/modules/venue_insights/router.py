"""`/venue/insights/*`: the logged-in venue member's own venue only (`nx_venue_*` cookies)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.common.dependencies import DBSession
from app.common.schemas import CustomResponse
from app.modules.auth.dependencies import CurrentVenueMember
from app.modules.venue_insights.schemas import GuestsOut, OverviewOut, Range, ScansOut
from app.modules.venue_insights.service import InsightsService

router = APIRouter(prefix="/venue/insights", tags=["Venue insights"])


def get_insights_service(session: DBSession) -> InsightsService:
    return InsightsService(session)


InsightsDep = Annotated[InsightsService, Depends(get_insights_service)]


@router.get(
    "/overview",
    response_model=CustomResponse[OverviewOut],
    summary="Visits, new guests and returning rate per day, the counter phone, latest scans",
)
async def overview(
    member: CurrentVenueMember, insights: InsightsDep, range: Range = Range.WEEK
) -> CustomResponse[OverviewOut]:
    return CustomResponse(message="OK", data=await insights.overview(member.venue_id, range))


@router.get(
    "/scans",
    response_model=CustomResponse[ScansOut],
    summary="Every scan attempt in the range and why it was or wasn't a Notch",
)
async def scans(
    member: CurrentVenueMember, insights: InsightsDep, range: Range = Range.WEEK
) -> CustomResponse[ScansOut]:
    return CustomResponse(message="OK", data=await insights.scans(member.venue_id, range))


@router.get(
    "/guests",
    response_model=CustomResponse[GuestsOut],
    summary="Guests as numbers (Guest #10) with their visit pattern; never who they are",
)
async def guests(member: CurrentVenueMember, insights: InsightsDep) -> CustomResponse[GuestsOut]:
    return CustomResponse(message="OK", data=await insights.guests(member.venue_id))
