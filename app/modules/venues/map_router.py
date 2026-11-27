"""`GET /venues/map`: the public map of a city's live places (venues/map.py)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from app.common.dependencies import DBSession, RedisClient
from app.common.http import client_ip
from app.common.schemas import CustomResponse
from app.core import rate_limit
from app.modules.locations.models import SLUG_REGEX
from app.modules.venues.exceptions import VenueMapRateLimitedException
from app.modules.venues.map import VenueMapService
from app.modules.venues.map_schemas import VenueMapOut
from app.modules.venues.models import VenueCategory
from app.modules.venues.repository import VenueRepository

router = APIRouter(prefix="/venues", tags=["Map"])

MAX_REQUESTS_PER_MINUTE = 60
# The same for everyone and changes rarely (a place goes live), so browsers may reuse it briefly.
CACHE_CONTROL = "public, max-age=300"


def get_map_service(session: DBSession) -> VenueMapService:
    return VenueMapService(VenueRepository(session))


@router.get(
    "/map",
    response_model=CustomResponse[VenueMapOut],
    summary="A city's live places as GeoJSON for the map, e.g. ?city=ahmedabad&category=cafe",
)
async def venue_map(
    request: Request,
    response: Response,
    redis: RedisClient,
    venues: Annotated[VenueMapService, Depends(get_map_service)],
    city: Annotated[
        str, Query(min_length=1, max_length=80, pattern=SLUG_REGEX, description="City slug")
    ],
    category: Annotated[
        VenueCategory | None, Query(description="Only this kind of place, e.g. cafe")
    ] = None,
) -> CustomResponse[VenueMapOut]:
    ip = client_ip(request)
    if ip and not await rate_limit.hit(
        redis, f"rl:venue-map:{ip}", limit=MAX_REQUESTS_PER_MINUTE, window_seconds=60
    ):
        raise VenueMapRateLimitedException()
    data = await venues.city_map(city, category)
    response.headers["Cache-Control"] = CACHE_CONTROL
    return CustomResponse(message="OK", data=data)
