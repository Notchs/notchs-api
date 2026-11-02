"""The public website over HTTP (A1-A15)."""

from datetime import UTC, datetime, timedelta

from fakeredis import FakeAsyncRedis
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.locations.time import today, week_of
from app.modules.users.models import SignupSource, User
from tests import factories as f


async def test_join_reserves_a_founding_number_once(api: AsyncClient, db: AsyncSession) -> None:
    city = await f.city(db)
    area = await f.area(db, city)
    body = {"name": "Riya Shah", "phone": "98765 43210", "area_id": str(area.id), "agree": True}

    first = await api.post("/public/join", json=body)
    assert first.status_code == 201
    assert first.json()["data"] == {"founding_member_no": 1, "founding_members_left": 499}
    user = await db.scalar(select(User).where(User.phone_e164 == "+919876543210"))
    assert user.signup_source == SignupSource.WEB and user.city_id == city.id
    assert user.consent_at is None  # the website never counts as WhatsApp consent

    again = await api.post("/public/join", json=body | {"name": "Someone Else"})
    assert again.status_code == 201
    assert again.json()["message"] == first.json()["message"]  # never says "already joined"
    assert again.json()["data"]["founding_member_no"] is None
    assert len((await db.scalars(select(User))).all()) == 1
    await db.refresh(user)
    assert user.name == "Riya Shah"  # never overwritten by a second sign-up

    assert (await api.post("/public/join", json=body | {"agree": False})).status_code == 422
    stats = (await api.get("/public/stats")).json()["data"]
    assert stats["founding_members_left"] == 499 and stats["founding_venues_total"] == 30


async def test_city_rankings_and_venue_page(
    api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    city = await f.city(db)
    area = await f.area(db, city)
    busy = await f.venue(db, city, name="Kahwa House", area_row=area)
    quiet = await f.venue(db, city, name="Blue Door", area_row=area)
    for venue in (busy, quiet):
        await f.counter(db, venue)
    last_week = week_of(today("Asia/Kolkata") - timedelta(days=7))
    noon = datetime.combine(last_week.start, datetime.min.time(), UTC) + timedelta(hours=6)
    for i in range(3):
        await f.notch_at(db, redis, await f.user(db), busy, noon + timedelta(minutes=i))
    await f.notch_at(db, redis, await f.user(db), quiet, noon)

    page = (await api.get("/public/cities/ahmedabad")).json()["data"]
    assert page["has_rankings"] is True
    assert [t["slug"] for t in page["categories"]] == ["cafes", "restaurants-and-bars", "turfs"]
    assert page["categories"][0]["venues_live"] == 2

    ranking = (await api.get("/public/cities/ahmedabad/rankings/cafes")).json()["data"]
    assert ranking["week_start"] == last_week.start.isoformat()
    assert [(r["rank"], r["venue"]["name"], r["visits"]) for r in ranking["items"]] == [
        (1, "Kahwa House", 3),
        (2, "Blue Door", 1),
    ]
    assert ranking["few_places"] is True
    by_area = await api.get(f"/public/cities/ahmedabad/rankings/cafes?area={area.slug}")
    assert by_area.json()["data"]["area"]["venues_live"] == 2
    assert (await api.get("/public/cities/ahmedabad/rankings/run-clubs")).status_code == 404
    bad_week = await api.get(f"/public/cities/ahmedabad/rankings/cafes?week={last_week.end}")
    assert bad_week.json()["error"]["code"] == "site.week_invalid"

    venue = (await api.get(f"/public/cities/ahmedabad/venues/{busy.slug}")).json()["data"]
    assert venue["name"] == "Kahwa House" and venue["first_notch_spots_left"] == 47
    assert venue["regular_now"] is None  # nobody has 5 visits, and nobody is public
    assert (await api.get("/public/cities/nowhere")).json()["error"][
        "code"
    ] == "site.city_not_found"


async def test_pulse_waits_for_the_first_full_month(api: AsyncClient) -> None:
    pulse = (await api.get("/public/pulse")).json()["data"]
    assert pulse["months"] == []
    assert pulse["first_pulse_on"].endswith("-01")


async def test_join_when_the_number_was_just_created_elsewhere(
    api: AsyncClient, db: AsyncSession
) -> None:
    # A second tab (or the bot) created the customer a moment before this request inserts:
    # the insert does nothing and the request still succeeds, without changing their name.
    await f.city(db)
    await f.user(db, phone_e164="+919876543210", name="Riya Shah")

    response = await api.post(
        "/public/join", json={"name": "Other", "phone": "+91 98765 43210", "agree": True}
    )

    assert response.status_code == 201
    assert response.json()["data"]["founding_member_no"] is None
    users = (await db.scalars(select(User))).all()
    assert [u.name for u in users] == ["Riya Shah"]
