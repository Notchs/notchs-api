"""`/me`: the logged-in customer's passport (WhatsApp login link, `nx_user_*` cookies).

Rank and leaderboard visibility stay in `/leaderboards/me`.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.common.dependencies import DBSession, RedisClient
from app.common.schemas import CustomResponse, PaginatedResponse
from app.modules.auth.cookies import clear_session_cookies
from app.modules.auth.dependencies import CurrentUser
from app.modules.auth.enums import SubjectType
from app.modules.passport.exceptions import PhoneChangeRateLimitedException
from app.modules.passport.schemas import (
    DeleteMeIn,
    MeOut,
    MeUpdate,
    NotchOut,
    PhoneChangeOut,
    RewardOut,
)
from app.modules.passport.service import PassportService
from app.modules.users.deletion import delete_customer
from app.modules.users.phone_change import COMMAND, PhoneChangeService

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


@router.post(
    "/phone-change",
    response_model=CustomResponse[PhoneChangeOut],
    summary="A code to move my account to a new number: send it from that number on WhatsApp",
)
async def phone_change(
    user: CurrentUser, session: DBSession, redis: RedisClient
) -> CustomResponse[PhoneChangeOut]:
    started = await PhoneChangeService(session, redis).start(user)
    if started is None:
        raise PhoneChangeRateLimitedException()
    return CustomResponse(
        message="Send this from your new number within 10 minutes.",
        data=PhoneChangeOut(
            message=f"{COMMAND} {started.code}",
            expires_at=started.expires_at,
            whatsapp_link=started.whatsapp_link,
        ),
    )


@router.post(
    "/delete",
    response_model=CustomResponse[None],
    summary='Delete my account for good (send {"confirm": "DELETE"}); logs me out',
)
async def delete_me(
    body: DeleteMeIn,
    user: CurrentUser,
    session: DBSession,
    redis: RedisClient,
    response: Response,
) -> CustomResponse[None]:
    await delete_customer(session, redis, user.id)
    clear_session_cookies(response, SubjectType.USER)
    return CustomResponse(message="Your account and personal data are deleted.")
