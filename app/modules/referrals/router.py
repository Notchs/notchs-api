"""`/me/referral`: the logged-in customer's invite card and who invited them."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.common.dependencies import DBSession
from app.common.schemas import CustomResponse
from app.modules.auth.dependencies import CurrentUser
from app.modules.referrals.schemas import InviterIn, ReferralOut
from app.modules.referrals.service import ReferralService

router = APIRouter(prefix="/me/referral", tags=["Passport"])


def get_referral_service(session: DBSession) -> ReferralService:
    return ReferralService(session)


ReferralDep = Annotated[ReferralService, Depends(get_referral_service)]


@router.get(
    "", response_model=CustomResponse[ReferralOut], summary="My invite link and Star Points"
)
async def card(user: CurrentUser, referrals: ReferralDep) -> CustomResponse[ReferralOut]:
    return CustomResponse(message="OK", data=await referrals.card(user))


@router.post(
    "/inviter",
    response_model=CustomResponse[ReferralOut],
    summary="Add who invited me (their invite code), once, in my first 14 days",
)
async def add_inviter(
    body: InviterIn, user: CurrentUser, referrals: ReferralDep
) -> CustomResponse[ReferralOut]:
    await referrals.add_inviter(user, body.code)
    return CustomResponse(message="Thanks! Saved.", data=await referrals.card(user))
