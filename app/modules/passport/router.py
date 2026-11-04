"""`/me`: the logged-in customer's passport (WhatsApp login link, `nx_user_*` cookies).

Rank and leaderboard visibility stay in `/leaderboards/me`.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.dependencies import DBSession
from app.common.schemas import CustomResponse, PaginatedResponse
from app.modules.auth.dependencies import CurrentUser
from app.modules.passport.schemas import MeOut, MeUpdate, NotchOut, RewardOut
from app.modules.passport.service import PassportService

router = APIRouter(prefix="/me", tags=["Passport"])


def get_passport_service(session: DBSession) -> PassportService:
    return PassportService(session)


PassportDep = Annotated[PassportService, Depends(get_passport_service)]


@router.get("", response_model=CustomResponse[MeOut], summary="My profile and totals")
async def me(user: CurrentUser, passport: PassportDep) -> CustomResponse[MeOut]:
    return CustomResponse(message="OK", data=await passport.me(user))


@router.patch(
    "",
    response_model=CustomResponse[MeOut],
    summary="Change my name, area, theme or Instagram (and whether venues see it)",
)
async def update_me(
    body: MeUpdate, user: CurrentUser, passport: PassportDep
) -> CustomResponse[MeOut]:
    return CustomResponse(message="Saved.", data=await passport.update_me(user, body))


@router.get(
    "/notches",
    response_model=CustomResponse[PaginatedResponse[NotchOut]],
    summary="My Notchs, newest first",
)
async def notches(
    user: CurrentUser,
    passport: PassportDep,
    page: Annotated[int, Query(ge=1, le=1000)] = 1,
    size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> CustomResponse[PaginatedResponse[NotchOut]]:
    return CustomResponse(message="OK", data=await passport.notches(user, page, size))


@router.get(
    "/reward",
    response_model=CustomResponse[RewardOut],
    summary="This month's progress to the free-food reward (5 new cafés)",
)
async def reward(user: CurrentUser, passport: PassportDep) -> CustomResponse[RewardOut]:
    return CustomResponse(message="OK", data=await passport.reward(user))
