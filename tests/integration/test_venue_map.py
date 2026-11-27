"""`GET /venues/map` on a real database: which places the public map shows, and its limits."""

import itertools
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.time import ist_date
from app.modules.locations.models import Area, City, CityStatus
from app.modules.scans.models import ScanSession, Visit, VisitStatus
from app.modules.users.models import User
from app.modules.venues.map_router import MAX_REQUESTS_PER_MINUTE
from app.modules.venues.models import Venue, VenueStatus
from tests.integration.test_scan_bot import add_customer, store

pytestmark = pytest.mark.asyncio(loop_scope="session")

_notch_numbers = itertools.count(1)


async def add_visit(
    db: AsyncSession,
    user: User,
    venue: Venue,
    *,
    days_ago: int = 0,
    status: str = VisitStatus.VALID,
) -> Visit:
    """A visit written straight to the tables, `days_ago` days before today in India."""
    message = await store(db, user.phone_e164, "x")
    scan = ScanSession(
        user_id=user.id, venue_id=venue.id, inbound_message_id=message.id, status="verified"
    )
    db.add(scan)
    await db.flush()
    day = ist_date(datetime.now(UTC)) - timedelta(days=days_ago)
    visit = Visit(
        user_id=user.id,
        venue_id=venue.id,
        scan_session_id=scan.id,
        visit_date=day,
        visited_at=datetime.now(UTC) - timedelta(days=days_ago),
        notch_no=next(_notch_numbers),
        visit_no_at_venue=1,
        code_slot=1,
        niche=venue.category,
        status=status,
        revoked_reason="Fake (test)" if status == VisitStatus.REVOKED else None,
    )
    db.add(visit)
    await db.commit()
    return visit


async def visits_by_new_customers(db: AsyncSession, venue: Venue, count: int) -> None:
    for _ in range(count):
        await add_visit(db, await add_customer(db), venue)


def ranks(body: dict) -> dict[str, int | None]:
    return {
        f["properties"]["name"]: f["properties"]["rank"] for f in body["data"]["venues"]["features"]
    }


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
            "rank": None,
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


# --- ranks ----------------------------------------------------------------------------------------


async def test_more_visits_rank_higher_and_places_without_visits_come_last(
    api: AsyncClient, db: AsyncSession
) -> None:
    city = await add_city(db)
    busy = await add_place(db, city, "Busy Cafe")
    quiet = await add_place(db, city, "Quiet Cafe")
    await add_place(db, city, "Amber Cafe")  # no visits: no rank, but still on the map
    await visits_by_new_customers(db, busy, 3)
    await visits_by_new_customers(db, quiet, 1)

    body = (await api.get("/venues/map", params={"city": city.slug})).json()

    assert names(body) == ["Busy Cafe", "Quiet Cafe", "Amber Cafe"]
    assert ranks(body) == {"Busy Cafe": 1, "Quiet Cafe": 2, "Amber Cafe": None}
    properties = body["data"]["venues"]["features"][0]["properties"]
    assert set(properties) == {"venue_code", "name", "slug", "category", "area", "rank"}


async def test_repeat_visits_count_but_one_customer_adds_at_most_8(
    api: AsyncClient, db: AsyncSession
) -> None:
    city = await add_city(db)
    regular_only = await add_place(db, city, "Regular Cafe")
    crowd = await add_place(db, city, "Crowd Cafe")
    returning = await add_place(db, city, "Returning Cafe")
    regular = await add_customer(db)
    for day in range(12):  # 12 visits, only 8 count
        await add_visit(db, regular, regular_only, days_ago=day)
    await visits_by_new_customers(db, crowd, 9)  # 9 people once: 9
    for customer in [await add_customer(db), await add_customer(db)]:
        for day in range(3):  # 2 people, 3 visits each: 6
            await add_visit(db, customer, returning, days_ago=day)

    body = (await api.get("/venues/map", params={"city": city.slug})).json()

    assert ranks(body) == {"Crowd Cafe": 1, "Regular Cafe": 2, "Returning Cafe": 3}


async def test_old_visits_fade_and_revoked_ones_never_count(
    api: AsyncClient, db: AsyncSession
) -> None:
    city = await add_city(db)
    fresh = await add_place(db, city, "Fresh Cafe")
    fading = await add_place(db, city, "Fading Cafe")
    gone = await add_place(db, city, "Gone Cafe")
    fraud = await add_place(db, city, "Fraud Cafe")
    await add_visit(db, await add_customer(db), fresh, days_ago=29)  # 100%
    await add_visit(db, await add_customer(db), fading, days_ago=40)  # 80%
    await add_visit(db, await add_customer(db), gone, days_ago=93)  # faded out
    for _ in range(5):
        await add_visit(db, await add_customer(db), fraud, status=VisitStatus.REVOKED)

    body = (await api.get("/venues/map", params={"city": city.slug})).json()

    assert ranks(body) == {
        "Fresh Cafe": 1,
        "Fading Cafe": 2,
        "Fraud Cafe": None,
        "Gone Cafe": None,
    }


async def test_equal_scores_go_to_more_customers_then_share_the_rank(
    api: AsyncClient, db: AsyncSession
) -> None:
    city = await add_city(db)
    two_people = await add_place(db, city, "Two People Cafe")
    one_regular = await add_place(db, city, "One Regular Cafe")
    twin = await add_place(db, city, "Twin Cafe")
    await visits_by_new_customers(db, two_people, 2)
    await visits_by_new_customers(db, twin, 2)
    regular = await add_customer(db)
    await add_visit(db, regular, one_regular, days_ago=0)
    await add_visit(db, regular, one_regular, days_ago=1)

    body = (await api.get("/venues/map", params={"city": city.slug})).json()

    assert ranks(body) == {"Two People Cafe": 1, "Twin Cafe": 1, "One Regular Cafe": 3}


async def test_kinds_are_ranked_apart_and_hidden_places_take_no_rank(
    api: AsyncClient, db: AsyncSession
) -> None:
    city = await add_city(db)
    cafe = await add_place(db, city, "Small Cafe")
    restaurant = await add_place(db, city, "Big Restaurant", category="restaurant")
    hidden = await add_place(db, city, "Hidden Cafe", visible_on_discover=False)
    await visits_by_new_customers(db, cafe, 1)
    await visits_by_new_customers(db, restaurant, 5)
    await visits_by_new_customers(db, hidden, 5)

    everything = (await api.get("/venues/map", params={"city": city.slug})).json()
    cafes = (await api.get("/venues/map", params={"city": city.slug, "category": "cafe"})).json()

    assert ranks(everything) == {"Big Restaurant": 1, "Small Cafe": 1}
    assert ranks(cafes) == {"Small Cafe": 1}
