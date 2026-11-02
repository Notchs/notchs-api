"""`/public`: what notchs.com shows (A1-A15). No login. Cache-friendly where it can be."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.clients.turnstile import TurnstileClient, get_turnstile_client
from app.common.dependencies import DBSession, RedisClient
from app.common.http import client_ip
from app.common.schemas import CustomResponse
from app.modules.auth.exceptions import CaptchaFailedException
from app.modules.site.schemas import (
    CityPageOut,
    JoinIn,
    JoinOut,
    PulseOut,
    RankingOut,
    RankingSort,
    StatsOut,
)
from app.modules.site.service import SiteService
from app.modules.venues.schemas import VenuePageOut

router = APIRouter(prefix="/public", tags=["Website"])

# Public pages may be cached briefly by the browser and the Next.js server.
SHORT_CACHE = "public, max-age=60"


def get_site_service(session: DBSession, redis: RedisClient) -> SiteService:
    return SiteService(session, redis)


SiteServiceDep = Annotated[SiteService, Depends(get_site_service)]
TurnstileClientDep = Annotated[TurnstileClient, Depends(get_turnstile_client)]


@router.post(
    "/join",
    status_code=status.HTTP_201_CREATED,
    response_model=CustomResponse[JoinOut],
    summary="Join the Founding 500 (A1 stop 5, A14)",
)
async def join(
    body: JoinIn, request: Request, service: SiteServiceDep, turnstile: TurnstileClientDep
) -> CustomResponse[JoinOut]:
    ip = client_ip(request)
    if not await turnstile.verify(body.turnstile_token, ip=ip):
        raise CaptchaFailedException()
    result = await service.join(body, ip=ip)
    # One message for everyone: it never says whether the number was already on the list.
    return CustomResponse(message="Thanks. You're on the list.", data=result)


@router.get(
    "/stats",
    response_model=CustomResponse[StatsOut],
    summary="Founding seats left: members (of 500) and venues (of 30)",
)
async def stats(service: SiteServiceDep, response: Response) -> CustomResponse[StatsOut]:
    response.headers["Cache-Control"] = SHORT_CACHE
    return CustomResponse(message="OK", data=await service.stats())


@router.get("/pulse", response_model=CustomResponse[PulseOut], summary="Notchs Pulse (A4)")
async def pulse(service: SiteServiceDep, response: Response) -> CustomResponse[PulseOut]:
    response.headers["Cache-Control"] = SHORT_CACHE
    return CustomResponse(message="OK", data=await service.pulse())


@router.get(
    "/cities/{city_slug}", response_model=CustomResponse[CityPageOut], summary="City page (A6)"
)
async def city(
    city_slug: str, service: SiteServiceDep, response: Response
) -> CustomResponse[CityPageOut]:
    response.headers["Cache-Control"] = SHORT_CACHE
    return CustomResponse(message="OK", data=await service.city_page(city_slug))


@router.get(
    "/cities/{city_slug}/rankings/{category}",
    response_model=CustomResponse[RankingOut],
    summary="Category ranking by verified visits in a week (A7): cafes, restaurants-and-bars, "
    "turfs, restaurants, bars, hotels",
)
async def ranking(
    city_slug: str,
    category: str,
    service: SiteServiceDep,
    response: Response,
    sort: RankingSort = "most_visited",
    area: str | None = None,
    week: date | None = None,
) -> CustomResponse[RankingOut]:
    response.headers["Cache-Control"] = SHORT_CACHE
    return CustomResponse(
        message="OK",
        data=await service.ranking(city_slug, category, sort=sort, area_slug=area, week_start=week),
    )


@router.get(
    "/cities/{city_slug}/venues/{venue_slug}",
    response_model=CustomResponse[VenuePageOut],
    summary="Venue page (A8)",
)
async def venue(
    city_slug: str, venue_slug: str, service: SiteServiceDep, response: Response
) -> CustomResponse[VenuePageOut]:
    response.headers["Cache-Control"] = SHORT_CACHE
    return CustomResponse(message="OK", data=await service.venue(city_slug, venue_slug))
