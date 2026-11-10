"""`/venue/profile` and `/venue/billing`: the logged-in owner's own place (venues/profile.py)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.common.dependencies import DBSession
from app.common.schemas import CustomResponse
from app.modules.auth.dependencies import CurrentVenueMember
from app.modules.venues.profile import VenueProfileService
from app.modules.venues.schemas import VenueBillingOut, VenueProfileOut, VenueProfileUpdate

router = APIRouter(prefix="/venue", tags=["Venue profile"])


def get_profile_service(session: DBSession) -> VenueProfileService:
    return VenueProfileService(session)


ProfileDep = Annotated[VenueProfileService, Depends(get_profile_service)]


@router.get("/profile", response_model=CustomResponse[VenueProfileOut], summary="My place")
async def profile(
    member: CurrentVenueMember, venues: ProfileDep
) -> CustomResponse[VenueProfileOut]:
    return CustomResponse(message="OK", data=await venues.profile(member))


@router.patch(
    "/profile",
    response_model=CustomResponse[VenueProfileOut],
    summary="Edit my place: name, description, address, landmark, area, opening hours",
)
async def update_profile(
    body: VenueProfileUpdate, member: CurrentVenueMember, venues: ProfileDep
) -> CustomResponse[VenueProfileOut]:
    return CustomResponse(message="Saved.", data=await venues.update(member, body))


@router.get(
    "/billing",
    response_model=CustomResponse[VenueBillingOut],
    summary="What I pay: nothing in the first 3 months, and my founding discount",
)
async def billing(
    member: CurrentVenueMember, venues: ProfileDep
) -> CustomResponse[VenueBillingOut]:
    return CustomResponse(message="OK", data=await venues.billing(member))
