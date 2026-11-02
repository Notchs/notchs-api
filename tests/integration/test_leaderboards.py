"""Scans step 7 on a real database: ranks from scans, rebuilding, and who the public list shows."""

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import main as cli_main
from app.common.time import ist_date
from app.modules.leaderboards.service import (
    LeaderboardService,
    Period,
    board_key,
    month_of,
    run_rebuilder,
)
from app.modules.locations.models import City
from app.modules.scans.models import PointsEntry, ScanSession, Visit
from app.modules.users.models import User
from app.modules.venues.models import Venue
from tests.integration.test_customer_auth import consume, cookie, link_for, token_of
from tests.integration.test_scan_bot import (
    add_customer,
    bot_under_test,  # noqa: F401  (fixture)
    code_now,
    meta,  # noqa: F401  (fixture)
    receive,
    scan_text,
    store,
)
from tests.integration.test_scan_pipeline import add_venue

pytestmark = pytest.mark.asyncio(loop_scope="session")

NOW = datetime(2026, 11, 15, 6, 30, tzinfo=UTC)


async def live_city(db: AsyncSession, venue: Venue) -> City:
    city = await db.get(City, venue.city_id)
    city.is_active = True
    await db.commit()
    return city


async def ledger_visit(
    db: AsyncSession, user: User, venue: Venue, points: int, at: datetime = NOW
) -> Visit:
    """A visit with points, written straight to the tables (not through Redis)."""
    message = await store(db, user.phone_e164, "x")
    scan = ScanSession(
        user_id=user.id, venue_id=venue.id, inbound_message_id=message.id, status="verified"
    )
    db.add(scan)
    await db.flush()
    visit = Visit(
        user_id=user.id,
        venue_id=venue.id,
        scan_session_id=scan.id,
        visit_date=at.date(),
        visited_at=at,
        notch_no=uuid.uuid4().int % 10**8 + 1,
        visit_no_at_venue=1,
        code_slot=1,
    )
    db.add(visit)
    await db.flush()
    db.add(PointsEntry(user_id=user.id, visit_id=visit.id, reason="visit", points=points))
    await db.commit()
    return visit


# --- from a scan ----------------------------------------------------------------------------------


async def test_a_stamp_says_the_real_rank(db, redis, bot_under_test, meta) -> None:  # noqa: F811
    venue, secret = await add_venue(db)
    city = await db.get(City, venue.city_id)
    ahead = await add_customer(db)
    late = await add_customer(db)

    await receive(db, bot_under_test, ahead.phone_e164, scan_text(venue, code_now(secret)))
    first = meta.texts()[-1]
    await LeaderboardService(redis).add_points(city.id, ahead.id, 50, ist_date(datetime.now(UTC)))
    await receive(db, bot_under_test, late.phone_e164, scan_text(venue, code_now(secret)))

    assert f"You're #1 in {city.name} this month." in first
    assert f"You're #2 in {city.name} this month." in meta.texts()[-1]


async def test_a_scan_adds_to_both_boards(db, redis, bot_under_test) -> None:  # noqa: F811
    venue, secret = await add_venue(db)
    user = await add_customer(db)

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))

    service = LeaderboardService(redis)
    month = await service.rank_of(venue.city_id, user.id, Period.MONTH)
    all_time = await service.rank_of(venue.city_id, user.id, Period.ALL)
    assert (month.rank, month.points) == (1, 12)
    assert (all_time.rank, all_time.points) == (1, 12)


async def test_a_refused_scan_adds_nothing(db, redis, bot_under_test, meta) -> None:  # noqa: F811
    venue, secret = await add_venue(db)
    user = await add_customer(db)
    code = code_now(secret)
    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code))

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code))

    rank = await LeaderboardService(redis).rank_of(venue.city_id, user.id, Period.ALL)
    assert rank.points == 12
    assert "#" not in meta.texts()[-1]


async def test_redis_failing_keeps_the_visit_and_the_stamp(
    db,
    redis,
    bot_under_test,  # noqa: F811
    meta,  # noqa: F811
    monkeypatch,
) -> None:
    async def fails(*_, **__) -> None:
        raise RedisError("down")

    monkeypatch.setattr(LeaderboardService, "add_points", fails)
    venue, secret = await add_venue(db)
    user = await add_customer(db)

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))

    assert any(text.startswith("Stamp added") for text in meta.texts())
    assert "You're #" not in meta.texts()[0]
    assert await db.scalar(select(Visit.id).where(Visit.user_id == user.id)) is not None


# --- ranks ----------------------------------------------------------------------------------------


async def test_equal_points_share_a_rank(db, redis) -> None:
    city_id, service = uuid.uuid4(), LeaderboardService(redis)
    first, tied_a, tied_b, last = [uuid.uuid4() for _ in range(4)]
    for user_id, points in ((first, 30), (tied_a, 20), (tied_b, 20), (last, 10)):
        await service.add_points(city_id, user_id, points, NOW.date())

    ranks = [
        (await service.rank_of(city_id, user_id, Period.ALL)).rank
        for user_id in (first, tied_a, tied_b, last)
    ]
    assert ranks == [1, 2, 2, 4]

    rows, total = await service.top(db, city_id, Period.ALL, 10)
    assert [row.rank for row in rows] == [1, 2, 2, 4]
    assert total == 4


async def test_no_points_no_rank(redis) -> None:
    assert await LeaderboardService(redis).rank_of(uuid.uuid4(), uuid.uuid4(), Period.ALL) is None


async def test_the_month_turns_at_midnight_in_india(redis) -> None:
    assert month_of(datetime(2026, 11, 30, 18, 29, tzinfo=UTC)) == "2026-11"
    assert month_of(datetime(2026, 11, 30, 18, 30, tzinfo=UTC)) == "2026-12"


async def test_cities_have_separate_boards(redis) -> None:
    service = LeaderboardService(redis)
    here, there, user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    await service.add_points(here, user_id, 10, NOW.date())

    assert await service.rank_of(there, user_id, Period.ALL) is None


# --- rebuilding -----------------------------------------------------------------------------------


async def test_rebuild_restores_an_emptied_redis(db, redis) -> None:
    venue, _ = await add_venue(db)
    high, low = await add_customer(db), await add_customer(db)
    await ledger_visit(db, high, venue, 30)
    await ledger_visit(db, low, venue, 10)
    service = LeaderboardService(redis)

    await service.rebuild(db, now=NOW)

    top = await service.rank_of(venue.city_id, high.id, Period.ALL, now=NOW)
    assert (top.rank, top.points) == (1, 30)
    month = await service.rank_of(venue.city_id, low.id, Period.MONTH, now=NOW)
    assert (month.rank, month.points) == (2, 10)


async def test_rebuild_fixes_wrong_scores_and_removes_stale_boards(db, redis) -> None:
    venue, _ = await add_venue(db)
    user = await add_customer(db)
    await ledger_visit(db, user, venue, 30)
    stale_city, service = uuid.uuid4(), LeaderboardService(redis)
    await service.add_points(venue.city_id, user.id, 999, NOW.date())
    await service.add_points(stale_city, user.id, 5, NOW.date())

    await service.rebuild(db, now=NOW)

    assert (await service.rank_of(venue.city_id, user.id, Period.ALL, now=NOW)).points == 30
    assert await redis.exists(board_key(stale_city, "all")) == 0


async def test_rebuild_leaves_other_redis_keys_alone(db, redis) -> None:
    await redis.set("rl:keep:me", "1")
    await redis.set("wa:notice:keep", "1")

    await LeaderboardService(redis).rebuild(db, now=NOW)

    assert await redis.get("rl:keep:me") == "1"
    assert await redis.get("wa:notice:keep") == "1"


async def test_this_month_only_counts_this_month(db, redis) -> None:
    venue, _ = await add_venue(db)
    user = await add_customer(db)
    await ledger_visit(db, user, venue, 30, NOW - timedelta(days=40))
    await ledger_visit(db, user, venue, 10, NOW)
    service = LeaderboardService(redis)

    await service.rebuild(db, now=NOW)

    assert (await service.rank_of(venue.city_id, user.id, Period.ALL, now=NOW)).points == 40
    assert (await service.rank_of(venue.city_id, user.id, Period.MONTH, now=NOW)).points == 10


async def test_revoked_visits_do_not_count(db, redis) -> None:
    venue, _ = await add_venue(db)
    user = await add_customer(db)
    visit = await ledger_visit(db, user, venue, 30)
    await ledger_visit(db, user, venue, 10, NOW + timedelta(days=1))
    visit.status = "revoked"
    await db.commit()
    service = LeaderboardService(redis)

    await service.rebuild(db, now=NOW)

    assert (await service.rank_of(venue.city_id, user.id, Period.ALL, now=NOW)).points == 10


@pytest.mark.parametrize("change", [{"status": "blocked"}, {"deleted_at": NOW}])
async def test_blocked_and_deleted_customers_leave_the_board(db, redis, change) -> None:
    venue, _ = await add_venue(db)
    user = await add_customer(db)
    await ledger_visit(db, user, venue, 30)
    for field, value in change.items():
        setattr(user, field, value)
    await db.commit()
    service = LeaderboardService(redis)

    await service.rebuild(db, now=NOW)

    assert await service.rank_of(venue.city_id, user.id, Period.ALL, now=NOW) is None


async def test_adjustments_count_in_the_home_city(db, redis) -> None:
    venue, _ = await add_venue(db)
    user = await add_customer(db)
    user.city_id = venue.city_id
    await ledger_visit(db, user, venue, 30)
    db.add(PointsEntry(user_id=user.id, reason="adjustment", points=-5))
    await db.commit()
    service = LeaderboardService(redis)

    await service.rebuild(db, now=NOW)

    assert (await service.rank_of(venue.city_id, user.id, Period.ALL, now=NOW)).points == 25


# --- the public list ------------------------------------------------------------------------------


async def test_private_customers_keep_their_place_without_a_name(
    api: AsyncClient, db, redis
) -> None:
    venue, _ = await add_venue(db)
    city = await live_city(db, venue)
    private = await add_customer(db)
    private.name = "Hidden Person"
    public = await add_customer(db)
    public.name, public.is_public = "Riya Mehta", True
    await db.commit()
    service = LeaderboardService(redis)
    await service.add_points(city.id, private.id, 30, ist_date(datetime.now(UTC)))
    await service.add_points(city.id, public.id, 20, ist_date(datetime.now(UTC)))

    response = await api.get("/leaderboards", params={"city_id": str(city.id)})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["total_ranked"] == 2
    assert data["period"] == "month"
    assert data["rows"] == [
        {"rank": 1, "points": 30, "name": None, "handle": None},
        {"rank": 2, "points": 20, "name": "Riya Mehta", "handle": public.handle},
    ]
    assert "Hidden Person" not in response.text
    assert private.handle not in response.text
    assert private.phone_e164 not in response.text
    assert public.phone_e164 not in response.text
    assert public.phone_e164.removeprefix("+") not in response.text


async def test_a_public_customer_who_is_blocked_shows_no_name(api: AsyncClient, db, redis) -> None:
    venue, _ = await add_venue(db)
    city = await live_city(db, venue)
    user = await add_customer(db)
    user.name, user.is_public, user.status = "Riya Mehta", True, "blocked"
    await db.commit()
    await LeaderboardService(redis).add_points(city.id, user.id, 30, ist_date(datetime.now(UTC)))

    response = await api.get("/leaderboards", params={"city_id": str(city.id)})

    assert "Riya Mehta" not in response.text


async def test_all_time_period_and_limit(api: AsyncClient, db, redis) -> None:
    venue, _ = await add_venue(db)
    city = await live_city(db, venue)
    service = LeaderboardService(redis)
    last_year = date(2025, 1, 15)
    for points in (30, 20, 10):
        await service.add_points(city.id, uuid.uuid4(), points, last_year)

    params = {"city_id": str(city.id), "period": "all", "limit": 2}
    response = await api.get("/leaderboards", params=params)

    data = response.json()["data"]
    assert [row["points"] for row in data["rows"]] == [30, 20]
    assert data["total_ranked"] == 3
    month = await api.get("/leaderboards", params={"city_id": str(city.id)})
    assert month.json()["data"]["rows"] == []


async def test_an_empty_board_is_really_empty(api: AsyncClient, db) -> None:
    venue, _ = await add_venue(db)
    city = await live_city(db, venue)

    response = await api.get("/leaderboards", params={"city_id": str(city.id)})

    assert response.json()["data"]["rows"] == []
    assert response.json()["data"]["total_ranked"] == 0


async def test_cities_not_live_have_no_board(api: AsyncClient, db) -> None:
    venue, _ = await add_venue(db)
    await db.commit()

    not_live = await api.get("/leaderboards", params={"city_id": str(venue.city_id)})
    unknown = await api.get("/leaderboards", params={"city_id": str(uuid.uuid4())})

    assert not_live.status_code == unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "leaderboards.city_not_found"


@pytest.mark.parametrize(
    "params",
    [{}, {"city_id": "nope"}, {"period": "week"}, {"limit": 0}, {"limit": 101}],
    ids=["no-city", "bad-city", "bad-period", "limit-0", "limit-101"],
)
async def test_bad_requests(api: AsyncClient, params) -> None:
    full = {"city_id": str(uuid.uuid4()), **params} if params.keys() - {"city_id"} else params

    assert (await api.get("/leaderboards", params=full)).status_code == 422


async def test_rate_limit(api: AsyncClient, redis) -> None:
    await redis.set("rl:leaderboard:203.0.113.7", 60, ex=60)

    response = await api.get("/leaderboards", params={"city_id": str(uuid.uuid4())})

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "leaderboards.rate_limited"


# --- a customer's own standing --------------------------------------------------------------------


async def logged_in(api: AsyncClient, db, redis, user: User) -> dict[str, str]:
    response = await consume(api, token_of(await link_for(db, redis, user)))
    api.cookies.clear()
    return {"nx_user_at": cookie(response, "nx_user_at")}


async def test_my_standing_before_any_visit(api: AsyncClient, db, redis) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)

    response = await api.get("/leaderboards/me", cookies=cookies)

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "city_id": None,
        "is_public": False,
        "month": None,
        "all_time": None,
    }


async def test_my_standing_after_a_visit(api: AsyncClient, db, redis, bot_under_test) -> None:  # noqa: F811
    venue, secret = await add_venue(db)
    user = await add_customer(db)
    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))
    cookies = await logged_in(api, db, redis, user)

    response = await api.get("/leaderboards/me", cookies=cookies)

    data = response.json()["data"]
    assert data["city_id"] == str(venue.city_id)
    assert data["month"] == {"rank": 1, "points": 12}
    assert data["all_time"] == {"rank": 1, "points": 12}


async def test_going_public_shows_my_name_and_going_private_hides_it(
    api: AsyncClient, db, redis
) -> None:
    venue, _ = await add_venue(db)
    city = await live_city(db, venue)
    user = await add_customer(db)
    user.name = "Riya Mehta"
    await db.commit()
    await LeaderboardService(redis).add_points(city.id, user.id, 30, ist_date(datetime.now(UTC)))
    cookies = await logged_in(api, db, redis, user)
    params = {"city_id": str(city.id)}
    assert "Riya Mehta" not in (await api.get("/leaderboards", params=params)).text

    public = await api.patch("/leaderboards/me", json={"is_public": True}, cookies=cookies)
    assert public.json()["data"]["is_public"] is True
    assert "Riya Mehta" in (await api.get("/leaderboards", params=params)).text

    await api.patch("/leaderboards/me", json={"is_public": False}, cookies=cookies)
    assert "Riya Mehta" not in (await api.get("/leaderboards", params=params)).text


async def test_my_standing_needs_a_login(api: AsyncClient) -> None:
    api.cookies.clear()
    assert (await api.get("/leaderboards/me")).status_code == 401
    assert (await api.patch("/leaderboards/me", json={"is_public": True})).status_code == 401


async def test_visibility_must_be_true_or_false(api: AsyncClient, db, redis) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)

    response = await api.patch("/leaderboards/me", json={}, cookies=cookies)

    assert response.status_code == 422


async def test_the_rebuild_loop_survives_a_failure(redis, monkeypatch) -> None:
    calls = []

    def failing_sessions() -> AsyncSession:
        calls.append(1)
        raise RuntimeError("the database is down")

    async def stop_after_two(_: float) -> None:
        if len(calls) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", stop_after_two)

    with pytest.raises(asyncio.CancelledError):
        await run_rebuilder(failing_sessions, redis, 3600)

    assert len(calls) == 2


def test_cli_lists_the_rebuild_command(capsys) -> None:
    with pytest.raises(SystemExit):
        cli_main(["--help"])

    assert "rebuild-leaderboards" in capsys.readouterr().out
