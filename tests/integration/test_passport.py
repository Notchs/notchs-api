"""The customer's passport (`/me`) on a real database: profile, settings, Notchs and the reward."""

import uuid
from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.locations.models import Area, City
from app.modules.passport.service import PassportService, _month_bounds
from app.modules.scans.models import Visit, VisitStatus
from app.modules.venues.models import Venue
from tests.integration.test_leaderboards import ledger_visit, logged_in
from tests.integration.test_scan_bot import (
    add_customer,
    bot_under_test,  # noqa: F401  (fixture)
    code_now,
    meta,  # noqa: F401  (fixture)
    receive,
    scan_text,
)
from tests.integration.test_scan_pipeline import add_venue as add_cafe

pytestmark = pytest.mark.asyncio(loop_scope="session")

NOW = datetime(2026, 11, 15, 6, 30, tzinfo=UTC)


async def add_venue(db: AsyncSession, **fields) -> tuple[Venue, str]:
    """A live venue, a café unless `category` says otherwise."""
    venue, secret = await add_cafe(db)
    for field, value in fields.items():
        setattr(venue, field, value)
    await db.commit()
    return venue, secret


async def add_area(db: AsyncSession, city_id: uuid.UUID, name: str = "Bodakdev") -> Area:
    area = Area(city_id=city_id, name=name, slug=f"a-{uuid.uuid4().hex[:8]}")
    db.add(area)
    await db.commit()
    return area


async def test_everything_needs_a_login(api: AsyncClient) -> None:
    api.cookies.clear()
    for method, path in [
        ("GET", "/me"),
        ("PATCH", "/me"),
        ("GET", "/me/notches"),
        ("GET", "/me/reward"),
    ]:
        response = await api.request(method, path, json={} if method == "PATCH" else None)
        assert response.status_code == 401, path


async def test_me_after_a_scan(api: AsyncClient, db, redis, bot_under_test) -> None:  # noqa: F811
    venue, secret = await add_venue(db)
    user = await add_customer(db)
    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))
    cookies = await logged_in(api, db, redis, user)

    data = (await api.get("/me", cookies=cookies)).json()["data"]

    assert data["totals"] == {"notches": 1, "places": 1, "points": 12}
    assert data["city"]["id"] == str(venue.city_id)
    assert data["is_public"] is False and data["show_instagram_to_venues"] is False
    assert user.phone_e164 not in str(data)


async def test_update_name_area_theme_and_instagram(api: AsyncClient, db, redis) -> None:
    venue, _ = await add_venue(db)
    area = await add_area(db, venue.city_id)
    user = await add_customer(db)
    user.city_id = venue.city_id
    await db.commit()
    cookies = await logged_in(api, db, redis, user)

    response = await api.patch(
        "/me",
        json={
            "name": "  Riya\nMehta ",
            "area_id": str(area.id),
            "theme": "dark",
            "instagram_handle": "@Riya.Eats",
            "show_instagram_to_venues": True,
        },
        cookies=cookies,
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["name"] == "Riya Mehta"
    assert data["area"] == {"id": str(area.id), "name": "Bodakdev"}
    assert data["theme"] == "dark"
    assert (data["instagram_handle"], data["show_instagram_to_venues"]) == ("riya.eats", True)


async def test_removing_instagram_also_hides_it(api: AsyncClient, db, redis) -> None:
    user = await add_customer(db)
    user.instagram_handle, user.show_instagram_to_venues = "riya.eats", True
    await db.commit()
    cookies = await logged_in(api, db, redis, user)

    data = (await api.patch("/me", json={"instagram_handle": None}, cookies=cookies)).json()

    assert data["data"]["instagram_handle"] is None
    assert data["data"]["show_instagram_to_venues"] is False


async def test_fields_not_sent_are_kept(api: AsyncClient, db, redis) -> None:
    user = await add_customer(db)
    user.name, user.instagram_handle = "Riya", "riya.eats"
    await db.commit()
    cookies = await logged_in(api, db, redis, user)

    data = (await api.patch("/me", json={"theme": "dark"}, cookies=cookies)).json()["data"]

    assert (data["name"], data["instagram_handle"]) == ("Riya", "riya.eats")


@pytest.mark.parametrize(
    "body",
    [
        {"name": "   "},
        {"name": "x" * 61},
        {"instagram_handle": "riya eats"},
        {"instagram_handle": "riya/eats"},
        {"instagram_handle": "a" * 31},
        {"theme": "pink"},
        {"area_id": "not-a-uuid"},
    ],
)
async def test_bad_settings_are_refused(api: AsyncClient, db, redis, body) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)

    response = await api.patch("/me", json=body, cookies=cookies)

    assert response.status_code == 422, response.text


async def test_an_area_of_another_city_is_refused(api: AsyncClient, db, redis) -> None:
    venue, _ = await add_venue(db)
    other_city = City(name="Rajkot", slug=f"rjt-{uuid.uuid4().hex[:6]}", state="Gujarat")
    db.add(other_city)
    await db.flush()
    elsewhere = await add_area(db, other_city.id, "Kalawad Road")
    user = await add_customer(db)
    user.city_id = venue.city_id
    await db.commit()
    cookies = await logged_in(api, db, redis, user)

    response = await api.patch("/me", json={"area_id": str(elsewhere.id)}, cookies=cookies)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "passport.area_invalid"


async def test_my_notches_newest_first_with_niche_colour(api: AsyncClient, db, redis) -> None:
    cafe, _ = await add_venue(db)
    restaurant, _ = await add_venue(db, category="restaurant", name="Agashiye")
    user = await add_customer(db)
    await ledger_visit(db, user, cafe, 12, at=datetime(2026, 11, 1, 6, tzinfo=UTC))
    await ledger_visit(db, user, restaurant, 10, at=datetime(2026, 11, 2, 6, tzinfo=UTC))
    cookies = await logged_in(api, db, redis, user)

    data = (await api.get("/me/notches", cookies=cookies)).json()["data"]

    assert data["total"] == 2
    assert [(n["venue_name"], n["colour"], n["points"]) for n in data["items"]] == [
        ("Agashiye", "violet", 10),
        ("Kahwa House", "blue", 12),
    ]


async def test_only_my_own_counted_notches(api: AsyncClient, db, redis) -> None:
    venue, _ = await add_venue(db)
    user = await add_customer(db)
    someone_else = await add_customer(db)
    await ledger_visit(db, someone_else, venue, 12)
    revoked = await ledger_visit(db, user, venue, 12)
    revoked.status = VisitStatus.REVOKED
    await db.commit()
    cookies = await logged_in(api, db, redis, user)

    data = (await api.get("/me/notches", cookies=cookies)).json()["data"]

    assert data["total"] == 0 and data["items"] == []


async def test_notches_are_paged(api: AsyncClient, db, redis) -> None:
    user = await add_customer(db)
    for day in range(1, 4):
        venue, _ = await add_venue(db)
        await ledger_visit(db, user, venue, 12, at=datetime(2026, 11, day, 6, tzinfo=UTC))
    cookies = await logged_in(api, db, redis, user)

    data = (await api.get("/me/notches?page=2&size=2", cookies=cookies)).json()["data"]

    assert (data["total"], data["pages"], len(data["items"])) == (3, 2, 1)
    assert (await api.get("/me/notches?size=51", cookies=cookies)).status_code == 422


async def _visit(db: AsyncSession, user, venue: Venue, day: int, visit_no: int = 1) -> Visit:
    visit = await ledger_visit(db, user, venue, 12, at=datetime(2026, 11, day, 6, tzinfo=UTC))
    visit.visit_no_at_venue = visit_no
    await db.commit()
    return visit


async def test_reward_counts_new_cafes_this_month(db) -> None:
    user = await add_customer(db)
    for day in range(1, 5):
        cafe, _ = await add_venue(db)
        await _visit(db, user, cafe, day)
    # Not new cafés: a repeat visit, a restaurant, and a café from last month.
    await _visit(db, user, cafe, 6, visit_no=2)
    restaurant, _ = await add_venue(db, category="restaurant")
    await _visit(db, user, restaurant, 7)
    old, _ = await add_venue(db)
    await ledger_visit(db, user, old, 12, at=datetime(2026, 10, 30, 6, tzinfo=UTC))

    reward = await PassportService(db).reward(user, NOW)

    assert (reward.month, reward.new_cafes, reward.needed, reward.eligible) == (
        "2026-11",
        4,
        5,
        False,
    )
    fifth, _ = await add_venue(db)
    await _visit(db, user, fifth, 8)
    assert (await PassportService(db).reward(user, NOW)).eligible is True


def test_month_bounds_cross_the_year() -> None:
    assert _month_bounds(date(2026, 12, 31)) == (date(2026, 12, 1), date(2027, 1, 1))


async def test_reward_month_is_the_month_in_india(db) -> None:
    user = await add_customer(db)
    # 00:30 on 1 December in India is still 30 November in UTC.
    reward = await PassportService(db).reward(user, datetime(2026, 11, 30, 19, 0, tzinfo=UTC))

    assert reward.month == "2026-12"


async def test_reward_endpoint(api: AsyncClient, db, redis) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)

    data = (await api.get("/me/reward", cookies=cookies)).json()["data"]

    assert (data["new_cafes"], data["needed"], data["value_rupees"]) == (0, 5, 249)
