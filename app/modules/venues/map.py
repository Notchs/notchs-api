"""The public map: a city's live places as GeoJSON points, with their rank.

A place shows only when it is live (`counter_live`), its owner left `visible_on_discover` on, it
has coordinates and it is not deleted. Ranks come from real visits (venues/ranking.py). Real rows
only: nothing is added or made up.

Each city's answer is kept in Redis for CACHE_SECONDS, so the visits are not re-counted on every
call. Without Redis the map still works: it is just counted every time.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.common.time import ist_date
from app.core.logging import get_logger
from app.modules.locations.models import City
from app.modules.venues.exceptions import VenueMapCityNotFoundException
from app.modules.venues.map_schemas import (
    LngLat,
    MapCity,
    MapPoint,
    MapVenueCollection,
    MapVenueFeature,
    MapVenueProperties,
    VenueMapOut,
)
from app.modules.venues.models import VenueCategory
from app.modules.venues.ranking import assign_ranks
from app.modules.venues.repository import VenueRepository

logger = get_logger(__name__)

# A safety cap on one response; a city is far below it.
MAX_VENUES_ON_MAP = 2000
CACHE_SECONDS = 60


def cache_key(city_slug: str, category: VenueCategory | None) -> str:
    return f"cache:venue-map:{city_slug}:{category.value if category else 'all'}"


def lng_lat(latitude: Decimal | None, longitude: Decimal | None) -> LngLat | None:
    if latitude is None or longitude is None:
        return None
    return (float(longitude), float(latitude))


def to_feature(row: Any, rank: int | None) -> MapVenueFeature:
    """One row from `VenueRepository.list_on_map` (always located) as a GeoJSON point."""
    return MapVenueFeature(
        geometry=MapPoint(coordinates=(float(row.longitude), float(row.latitude))),
        properties=MapVenueProperties(
            venue_code=row.venue_code,
            name=row.name,
            slug=row.slug,
            category=row.category,
            area=row.area,
            rank=rank,
        ),
    )


def to_map_city(city: City) -> MapCity:
    return MapCity(slug=city.slug, name=city.name, center=lng_lat(city.latitude, city.longitude))


def _order(feature: MapVenueFeature) -> tuple[bool, int, str]:
    rank = feature.properties.rank
    return (rank is None, rank or 0, feature.properties.name.casefold())


class VenueMapService:
    def __init__(self, repo: VenueRepository, redis: Redis | None = None) -> None:
        self.repo = repo
        self.redis = redis

    async def city_map(
        self, city_slug: str, category: VenueCategory | None, *, today: date | None = None
    ) -> VenueMapOut:
        """`today` (tests) counts the ranks for another day, never from or into the cache."""
        key = cache_key(city_slug, category)
        if today is None and (cached := await self._cached(key)) is not None:
            return cached
        out = await self._build(city_slug, category, today)
        if today is None:
            await self._store(key, out)
        return out

    async def _cached(self, key: str) -> VenueMapOut | None:
        if self.redis is None:
            return None
        try:
            raw = await self.redis.get(key)
        except RedisError:
            logger.warning("Map cache skipped: Redis did not answer")
            return None
        return VenueMapOut.model_validate_json(raw) if raw else None

    async def _store(self, key: str, out: VenueMapOut) -> None:
        if self.redis is None:
            return
        try:
            await self.redis.set(key, out.model_dump_json(), ex=CACHE_SECONDS)
        except RedisError:
            logger.warning("Map not cached: Redis did not answer")

    async def _build(
        self, city_slug: str, category: VenueCategory | None, today: date | None
    ) -> VenueMapOut:
        city = await self.repo.get_live_city_by_slug(city_slug)
        if city is None:
            raise VenueMapCityNotFoundException()
        rows = await self.repo.list_on_map(
            city.id,
            category=category.value if category is not None else None,
            limit=MAX_VENUES_ON_MAP,
        )
        scores = await self.repo.visit_scores(
            [row.id for row in rows], today or ist_date(datetime.now(UTC))
        )
        ranks = assign_ranks((row.id, row.category, scores.get(row.id)) for row in rows)
        features = sorted((to_feature(row, ranks.get(row.id)) for row in rows), key=_order)
        return VenueMapOut(city=to_map_city(city), venues=MapVenueCollection(features=features))
