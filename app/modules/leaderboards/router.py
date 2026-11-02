"""`/leaderboards`: the public list per city, and a logged-in customer's own standing."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import select

from app.common.dependencies import DBSession, RedisClient
from app.common.http import client_ip
from app.common.schemas import CustomResponse
from app.core import rate_limit
from app.modules.auth.dependencies import CurrentUser
from app.modules.leaderboards.exceptions import (
    LeaderboardCityNotFoundException,
    LeaderboardRateLimitedException,
)
from app.modules.leaderboards.schemas import (
    LeaderboardOut,
    LeaderboardRow,
    MyRank,
    MyStandingOut,
    VisibilityIn,
)
from app.modules.leaderboards.service import LeaderboardService, Period
from app.modules.locations.models import City

router = APIRouter(prefix="/leaderboards", tags=["Leaderboards"])

MAX_REQUESTS_PER_MINUTE = 60
CACHE_CONTROL = "public, max-age=30"


@router.get(
    "",
    response_model=CustomResponse[LeaderboardOut],
    summary="A city's top customers, this month or all time",
)
async def leaderboard(
    request: Request,
    response: Response,
    session: DBSession,
    redis: RedisClient,
    city_id: uuid.UUID,
    period: Period = Period.MONTH,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CustomResponse[LeaderboardOut]:
    ip = client_ip(request)
    if ip and not await rate_limit.hit(
        redis, f"rl:leaderboard:{ip}", limit=MAX_REQUESTS_PER_MINUTE, window_seconds=60
    ):
        raise LeaderboardRateLimitedException()
    live = await session.scalar(select(City.id).where(City.id == city_id, City.is_active.is_(True)))
    if live is None:
        raise LeaderboardCityNotFoundException()
    rows, total = await LeaderboardService(redis).top(session, city_id, period, limit)
    response.headers["Cache-Control"] = CACHE_CONTROL
    return CustomResponse(
        message="OK",
        data=LeaderboardOut(
            city_id=city_id,
            period=period,
            total_ranked=total,
            rows=[LeaderboardRow.model_validate(row) for row in rows],
        ),
    )


async def _standing(user, redis) -> MyStandingOut:
    month = all_time = None
    if user.city_id is not None:
        service = LeaderboardService(redis)
        month = await service.rank_of(user.city_id, user.id, Period.MONTH)
        all_time = await service.rank_of(user.city_id, user.id, Period.ALL)
    return MyStandingOut(
        city_id=user.city_id,
        is_public=user.is_public,
        month=MyRank.model_validate(month) if month else None,
        all_time=MyRank.model_validate(all_time) if all_time else None,
    )


@router.get("/me", response_model=CustomResponse[MyStandingOut], summary="My rank in my home city")
async def my_standing(user: CurrentUser, redis: RedisClient) -> CustomResponse[MyStandingOut]:
    return CustomResponse(message="OK", data=await _standing(user, redis))


@router.patch(
    "/me",
    response_model=CustomResponse[MyStandingOut],
    summary="Choose whether my name shows on the leaderboard (private by default)",
)
async def set_visibility(
    body: VisibilityIn, user: CurrentUser, session: DBSession, redis: RedisClient
) -> CustomResponse[MyStandingOut]:
    user.is_public = body.is_public
    await session.flush()
    return CustomResponse(message="Saved.", data=await _standing(user, redis))
