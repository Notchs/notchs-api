"""The owner's own place: editing its details (never its numbers) and what it pays."""

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.modules.locations.models import Area, City
from app.modules.venues.models import Venue
from tests.integration.test_counter import owner  # noqa: F401  (fixture)

pytestmark = pytest.mark.asyncio(loop_scope="session")

HOURS = {"mon": [["08:00", "23:00"]], "sat": [["10:00", "01:00"]]}


async def test_needs_a_venue_login(api: AsyncClient) -> None:
    api.cookies.clear()
    for path in ("/venue/profile", "/venue/billing"):
        assert (await api.get(path)).status_code == 401
    assert (await api.patch("/venue/profile", json={"name": "X"})).status_code == 401


async def test_see_and_edit_my_place(api: AsyncClient, db, owner) -> None:  # noqa: F811
    cookies = owner["cookies"]
    venue = await db.get(Venue, owner["venue_id"])
    area = Area(city_id=venue.city_id, name="Navrangpura", slug="navrangpura")
    db.add(area)
    await db.commit()
    area_id, scan_code = str(area.id), venue.scan_code

    before = (await api.get("/venue/profile", cookies=cookies)).json()["data"]
    response = await api.patch(
        "/venue/profile",
        json={
            "name": "Kahwa House & Kitchen",
            "description": "Slow coffee, fast Wi-Fi.",
            "address": "12 CG Road",
            "landmark": "Opposite Law Garden",
            "area_id": area_id,
            "opening_hours": HOURS,
        },
        cookies=cookies,
    )

    assert (before["name"], before["niche"], before["category"]) == (
        "Kahwa House",
        "café",
        "cafe",
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["name"] == "Kahwa House & Kitchen"
    assert (data["area"], data["landmark"], data["opening_hours"]) == (
        "Navrangpura",
        "Opposite Law Garden",
        HOURS,
    )
    # The QR's scan code and the kind of place don't change.
    assert (data["scan_code"], data["category"]) == (scan_code, "cafe")


async def test_numbers_and_kind_cant_be_edited(api: AsyncClient, db, owner) -> None:  # noqa: F811
    cookies = owner["cookies"]

    for body in (
        {"category": "bar"},
        {"scan_code": "FREE"},
        {"founding_venue_no": 1},
        {"status": "counter_live"},
        {"monthly_fee_paise": 0},
    ):
        response = await api.patch("/venue/profile", json=body, cookies=cookies)
        assert response.status_code == 200, body  # unknown fields are ignored...
    data = (await api.get("/venue/profile", cookies=cookies)).json()["data"]
    assert (data["category"], data["status"]) == ("cafe", "signed")  # ...and change nothing
    assert (await db.get(Venue, owner["venue_id"])).founding_venue_no is None


@pytest.mark.parametrize(
    "body",
    [
        {"name": None},
        {"name": "K"},
        {"description": "x" * 161},
        {"opening_hours": {"mon": [["09:00", "09:00"]]}},
        {"opening_hours": {"someday": [["09:00", "10:00"]]}},
    ],
)
async def test_bad_details(api: AsyncClient, owner, body: dict) -> None:  # noqa: F811
    response = await api.patch("/venue/profile", json=body, cookies=owner["cookies"])

    assert response.status_code == 422


async def test_area_must_be_in_my_city(api: AsyncClient, db, owner) -> None:  # noqa: F811
    elsewhere = City(name="Surat", slug="surat-x", state="Gujarat")
    db.add(elsewhere)
    await db.flush()
    area = Area(city_id=elsewhere.id, name="Adajan", slug="adajan")
    db.add(area)
    await db.commit()

    response = await api.patch(
        "/venue/profile", json={"area_id": str(area.id)}, cookies=owner["cookies"]
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "venues.location_invalid"


async def test_billing(api: AsyncClient, db, owner) -> None:  # noqa: F811
    cookies = owner["cookies"]
    plain = (await api.get("/venue/billing", cookies=cookies)).json()["data"]
    await db.execute(update(Venue).where(Venue.id == owner["venue_id"]).values(founding_venue_no=7))
    await db.commit()

    founding = (await api.get("/venue/billing", cookies=cookies)).json()["data"]

    assert plain["amount_due_paise"] == 0
    assert plain["free_months"] == 3
    assert (plain["founding_no"], plain["founding_spots"], plain["niche"]) == (None, 100, "café")
    assert founding["founding_no"] == 7
    assert founding["founding_discount_percent"] == 25
    assert founding["founding_spots_left"] <= 93
