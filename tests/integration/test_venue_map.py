"""`GET /venues/map` on a real database: which places the public map shows, and its limits."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.locations.models import Area, City, CityStatus
from app.modules.venues.map_router import MAX_REQUESTS_PER_MINUTE
from app.modules.venues.models import Venue, VenueStatus

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def add_city(db: AsyncSession, status: str = CityStatus.LIVE) -> City:
    city = City(
        name="Ahmedabad",
        slug=f"ahd-{uuid.uuid4().hex[:6]}",
        state="Gujarat",
        status=status,
        latitude=Decimal("23.022505"),
        longitude=Decimal("72.571365"),
    )
    db.add(city)
    await db.flush()
    return city


async def add_place(db: AsyncSession, city: City, name: str, **fields) -> Venue:
    data = {
        "category": "cafe",
        "status": VenueStatus.COUNTER_LIVE,
        "latitude": Decimal("23.033863"),
        "longitude": Decimal("72.585022"),
    } | fields
    venue = Venue(city_id=city.id, name=name, slug=name.lower().replace(" ", "-"), **data)
    db.add(venue)
    await db.flush()
    return venue


def names(body: dict) -> list[str]:
    return [f["properties"]["name"] for f in body["data"]["venues"]["features"]]


async def test_shows_only_live_visible_located_places_in_that_city(
    api: AsyncClient, db: AsyncSession
) -> None:
    city = await add_city(db)
    area = Area(city_id=city.id, name="SBR", slug="sbr")
    db.add(area)
    await db.flush()
    shown = await add_place(db, city, "Kahwa House", area_id=area.id)
    await add_place(db, city, "Signed Not Live", status=VenueStatus.SIGNED)
    await add_place(db, city, "Paused Cafe", status=VenueStatus.PAUSED)
    await add_place(db, city, "Closed Cafe", status=VenueStatus.CLOSED)
    await add_place(db, city, "Hidden Cafe", visible_on_discover=False)
    await add_place(db, city, "No Pin Cafe", latitude=None, longitude=None)
    await add_place(db, city, "Deleted Cafe", deleted_at=datetime.now(UTC))
    other = await add_city(db)
    await add_place(db, other, "Elsewhere Cafe")
    await db.commit()

    response = await api.get("/venues/map", params={"city": city.slug})

    assert response.status_code == 200
    body = response.json()
    assert names(body) == ["Kahwa House"]
    assert body["data"]["city"] == {
        "slug": city.slug,
        "name": "Ahmedabad",
        "center": [72.571365, 23.022505],
    }
    assert body["data"]["venues"]["features"][0] == {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [72.585022, 23.033863]},
        "properties": {
            "venue_code": shown.venue_code,
            "name": "Kahwa House",
            "slug": "kahwa-house",
            "category": "cafe",
            "area": "SBR",
        },
    }
    assert response.headers["cache-control"] == "public, max-age=300"


async def test_category_narrows_the_map(api: AsyncClient, db: AsyncSession) -> None:
    city = await add_city(db)
    await add_place(db, city, "Kahwa House")
    await add_place(db, city, "Thali Ghar", category="restaurant")
    await db.commit()

    cafes = await api.get("/venues/map", params={"city": city.slug, "category": "cafe"})
    everything = await api.get("/venues/map", params={"city": city.slug})

    assert names(cafes.json()) == ["Kahwa House"]
    assert names(everything.json()) == ["Kahwa House", "Thali Ghar"]


async def test_an_unknown_kind_of_place_is_rejected(api: AsyncClient, db: AsyncSession) -> None:
    city = await add_city(db)
    await db.commit()
    response = await api.get("/venues/map", params={"city": city.slug, "category": "casino"})
    assert response.status_code == 422


@pytest.mark.parametrize("status", [CityStatus.HIDDEN, CityStatus.COMING_SOON])
async def test_cities_that_are_not_live_have_no_map(
    api: AsyncClient, db: AsyncSession, status: str
) -> None:
    city = await add_city(db, status=status)
    await add_place(db, city, "Kahwa House")
    await db.commit()

    hidden = await api.get("/venues/map", params={"city": city.slug})
    unknown = await api.get("/venues/map", params={"city": "no-such-city"})

    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json() == unknown.json()
    assert hidden.json()["error"]["code"] == "venues.map_city_not_found"


async def test_a_city_with_no_live_places_has_an_empty_map(
    api: AsyncClient, db: AsyncSession
) -> None:
    city = await add_city(db)
    await db.commit()
    response = await api.get("/venues/map", params={"city": city.slug})
    assert response.status_code == 200
    assert response.json()["data"]["venues"] == {"type": "FeatureCollection", "features": []}


async def test_too_many_calls_from_one_address_are_refused(
    api: AsyncClient, db: AsyncSession, redis
) -> None:
    city = await add_city(db)
    await db.commit()
    # The test client always comes from 203.0.113.7 (tests/integration/conftest.py).
    await redis.set("rl:venue-map:203.0.113.7", MAX_REQUESTS_PER_MINUTE, ex=60)

    response = await api.get("/venues/map", params={"city": city.slug})

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "venues.map_rate_limited"
