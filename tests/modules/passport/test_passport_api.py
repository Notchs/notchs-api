"""The passport over HTTP (C1-C14)."""

from datetime import UTC, datetime, timedelta

from fakeredis import FakeAsyncRedis
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.locations.time import local
from app.modules.notches.models import Notch
from app.modules.users.models import User, UserStatus
from tests import factories as f


async def _world(db: AsyncSession, redis: FakeAsyncRedis):
    city = await f.city(db)
    area = await f.area(db, city)
    kahwa = await f.venue(db, city, name="Kahwa House", area_row=area)
    turf = await f.venue(db, city, name="Sabar Turf", area_row=area, latitude=23.03, longitude=72.5)
    for venue in (kahwa, turf):
        await f.counter(db, venue)
    user = await f.user(db)
    # Two visits to Kahwa House this month (on two different days, in IST).
    now = datetime.now(UTC)
    first_day = local(now, "Asia/Kolkata").replace(
        day=1, hour=10, minute=0, second=0, microsecond=0
    )
    # On the 1st itself there is only one day so far this month, so only one visit.
    moments = [first_day.astimezone(UTC), now] if local(now, None).day > 1 else [now]
    for moment in moments:
        await f.notch_at(db, redis, user, kahwa, moment)
    return city, area, kahwa, turf, user


async def test_needs_login(api: AsyncClient) -> None:
    response = await api.get("/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.not_authenticated"


async def test_claim_and_home(api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis) -> None:
    _, area, kahwa, _, user = await _world(db, redis)
    api.cookies = f.login(user)

    me = (await api.get("/me")).json()["data"]
    assert me["needs_name"] is True
    assert me["first_notch"]["venue"]["name"] == "Kahwa House"
    assert me["founding_member_no"] == 1

    claimed = await api.patch("/me", json={"name": "Riya Shah", "area_id": str(area.id)})
    assert claimed.status_code == 200
    assert claimed.json()["data"]["needs_name"] is False
    assert (await api.patch("/me", json={"name": None})).status_code == 422

    home = (await api.get("/me/home")).json()["data"]
    visits = home["notches_this_month"]
    assert home["first_name"] == "Riya"
    assert home["rank"] is None and home["notches_to_rank"] == 3 - visits
    assert home["next_step"]["venue"]["venue_code"] == kahwa.venue_code
    assert home["next_step"]["left"] == 5 - visits
    assert home["unnotched"]["venue"]["name"] == "Sabar Turf"


async def test_history_detail_and_report(
    api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    *_, user = await _world(db, redis)
    api.cookies = f.login(user)

    history = (await api.get("/me/notches")).json()["data"]
    assert history["total"] == len(history["items"]) >= 1
    assert history["all_time_places"] == 1
    code = history["items"][0]["notch_code"]

    detail = (await api.get(f"/me/notches/{code.lower()}")).json()["data"]
    assert detail["venue"]["name"] == "Kahwa House" and detail["under_review"] is False

    reported = await api.post(f"/me/notches/{code}/report", json={"message": "Wrong place"})
    assert reported.json()["data"]["under_review"] is True
    assert (await api.get("/me/notches?month=2026-13")).status_code == 400


async def test_badges_leaderboard_places_and_venue(
    api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    _, _, kahwa, _, user = await _world(db, redis)
    api.cookies = f.login(user)

    badges = (await api.get("/me/badges")).json()["data"]
    earned = {b["badge"] for b in badges["earned"]}
    assert {"first_notch", "founding_member"} <= earned
    regular = next(b for b in badges["locked"] if b["badge"] == "regular")
    assert regular["progress"]["target"] == 5 and regular["venue"]["name"] == "Kahwa House"

    board = (await api.get("/me/leaderboard?period=all")).json()["data"]
    assert board["you"]["rank"] == 1 and board["top"][0]["name"] == "You"

    places = (await api.get("/me/places?lat=23.03&lng=72.5")).json()["data"]
    assert places["near_you"] is True
    assert places["items"][0]["venue"]["name"] == "Sabar Turf"  # the one with coordinates
    assert places["items"][0]["distance_m"] == 0
    tags = {p["venue"]["name"]: p["tag"] for p in places["items"]}
    assert tags["Kahwa House"].startswith("You're ")
    assert tags["Sabar Turf"] == "First Notch spots left"

    venue = (await api.get(f"/me/venues/{kahwa.venue_code}")).json()["data"]
    assert venue["you"]["visits_total"] >= 1
    assert venue["first_notch_spots_left"] == 49


async def test_year_export_and_delete(
    api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    *_, user = await _world(db, redis)
    api.cookies = f.login(user)
    year = datetime.now(UTC).year

    recap = (await api.get(f"/me/year/{year}")).json()["data"]
    assert recap["place_of_the_year"]["name"] == "Kahwa House"

    export = await api.get("/me/export")
    assert export.headers["content-disposition"].startswith("attachment")
    assert export.json()["profile"]["phone"] == user.phone_e164

    assert (await api.post("/me/delete", json={"confirm": False})).status_code == 422
    deleted = await api.post("/me/delete", json={"confirm": True})
    assert deleted.status_code == 200
    assert "nx_user_at" in deleted.headers.get("set-cookie", "")
    row = await db.scalar(select(User).where(User.id == user.id))
    assert row.status == UserStatus.DELETED
    assert (await api.get("/me")).status_code == 403


async def test_website_founding_member_sees_the_badge_before_a_notch(
    api: AsyncClient, db: AsyncSession
) -> None:
    await f.city(db)
    user = await f.user(db, consented=False, founding_member_no=7)
    api.cookies = f.login(user)

    badges = (await api.get("/me/badges")).json()["data"]
    (founding,) = [b for b in badges["earned"] if b["badge"] == "founding_member"]
    assert founding["detail"] == "#7 of 500"
    assert (await api.get("/me")).json()["data"]["badges_count"] == 1


async def test_blocked_customers_leave_the_leaderboard(
    api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    _, _, kahwa, _, user = await _world(db, redis)
    other = await f.user(db)
    for days in (0, 1, 2):
        await f.notch_at(db, redis, other, kahwa, datetime.now(UTC) - timedelta(days=days))
    other.status = UserStatus.BLOCKED
    await db.flush()
    api.cookies = f.login(user)

    board = (await api.get("/me/leaderboard?period=all")).json()["data"]
    assert [row["is_you"] for row in board["top"]] == [True]


async def test_reporting_twice_records_it_once(
    api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    *_, user = await _world(db, redis)
    api.cookies = f.login(user)
    code = (await api.get("/me/notches")).json()["data"]["items"][0]["notch_code"]

    for _ in range(2):
        await api.post(f"/me/notches/{code}/report", json={"message": "Wrong place"})

    notch = await db.scalar(select(Notch).where(Notch.notch_code == code))
    assert notch.flag_note.count("Wrong place") == 1


async def test_someone_elses_notch_is_not_found(
    api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    *_, user = await _world(db, redis)
    code = await db.scalar(select(Notch.notch_code).where(Notch.user_id == user.id))
    api.cookies = f.login(await f.user(db))

    assert (await api.get(f"/me/notches/{code}")).status_code == 404
    assert (
        await api.post(f"/me/notches/{code}/report", json={"message": "x" * 5})
    ).status_code == 404
