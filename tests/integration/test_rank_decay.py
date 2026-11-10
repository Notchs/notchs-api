"""Rank decay: after 15 days without a Notch, rank points drop 10% a week; a Notch restores them."""

from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.common.time import ist_date
from app.modules.leaderboards.decay import kept_percent, next_drop_on, rank_points
from app.modules.leaderboards.service import ALL_TIME, LeaderboardService, board_key, month_of
from tests.integration.test_leaderboards import ledger_visit, logged_in
from tests.integration.test_scan_bot import add_customer
from tests.integration.test_scan_pipeline import add_venue

pytestmark = pytest.mark.asyncio(loop_scope="session")

DAY = date(2026, 11, 1)


@pytest.mark.parametrize(
    ("idle_days", "kept", "drops_in"),
    [
        (0, 100, 16),
        (15, 100, 1),
        (16, 90, 7),
        (22, 90, 1),
        (23, 80, 7),
        (100, 0, None),
    ],
)
def test_the_rule(idle_days: int, kept: int, drops_in: int | None) -> None:
    today = DAY + timedelta(days=idle_days)

    assert kept_percent(DAY, today) == kept
    drop = next_drop_on(DAY, today)
    assert ((drop - today).days if drop else None) == drops_in
    if drop:  # the next drop really is 10% less
        assert kept_percent(DAY, drop) == kept - 10


def test_no_notch_yet_never_decays() -> None:
    assert (kept_percent(None, DAY), next_drop_on(None, DAY)) == (100, None)


def test_rank_points_round_down() -> None:
    assert (rank_points(25, 90), rank_points(25, 100), rank_points(25, 0)) == (22, 25, 0)


async def test_rebuild_decays_and_a_new_notch_restores(db, redis) -> None:
    venue, _ = await add_venue(db)
    idle, regular = await add_customer(db), await add_customer(db)
    now = datetime.now(UTC)
    await ledger_visit(db, idle, venue, 100, at=now - timedelta(days=17))
    await ledger_visit(db, regular, venue, 100, at=now - timedelta(days=3))
    board = board_key(venue.city_id, ALL_TIME)
    service = LeaderboardService(redis)

    await service.rebuild(db, now=now)

    assert await redis.zscore(board, str(regular.id)) == 100
    assert await redis.zscore(board, str(idle.id)) == 90
    # A new Notch: the exact score goes back on, all of it.
    await ledger_visit(db, idle, venue, 12, at=now)
    await service.sync_user(db, idle.id, now=now)
    assert await redis.zscore(board, str(idle.id)) == 112
    assert await redis.zscore(board_key(venue.city_id, month_of(now)), str(idle.id)) >= 12


async def test_my_standing_says_when_it_drops(api: AsyncClient, db, redis) -> None:
    venue, _ = await add_venue(db)
    user = await add_customer(db)
    now = datetime.now(UTC)
    last = now - timedelta(days=20)
    await ledger_visit(db, user, venue, 50, at=last)
    cookies = await logged_in(api, db, redis, user)

    data = (await api.get("/leaderboards/me", cookies=cookies)).json()["data"]

    assert data["rank_points_percent"] == 90
    assert data["rank_drops_on"] == (ist_date(last) + timedelta(days=23)).isoformat()
