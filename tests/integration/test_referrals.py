"""Invites and Star Points: adding an inviter, the reward on the first real Notch, and abuse."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.modules.referrals.models import Referral, StarPointsEntry
from app.modules.referrals.service import (
    MAX_REWARDS_PER_MONTH,
    REFERRAL_STAR_POINTS,
    reward_first_notch,
)
from app.modules.users.models import User
from tests.integration.test_leaderboards import ledger_visit, logged_in
from tests.integration.test_scan_bot import (
    add_customer,
    bot_under_test,  # noqa: F401  (fixture)
    code_now,
    meta,  # noqa: F401  (fixture)
    receive,
    scan_text,
)
from tests.integration.test_scan_pipeline import add_venue, phone
from tests.integration.test_scan_races import sessions  # noqa: F401  (fixture)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def star_points(db, user: User) -> int:
    return (
        await db.scalar(
            select(func.coalesce(func.sum(StarPointsEntry.points), 0)).where(
                StarPointsEntry.user_id == user.id
            )
        )
        or 0
    )


async def add_inviter(api: AsyncClient, cookies, code: str):
    return await api.post("/me/referral/inviter", json={"code": code}, cookies=cookies)


async def test_needs_a_login(api: AsyncClient) -> None:
    api.cookies.clear()
    assert (await api.get("/me/referral")).status_code == 401
    assert (await api.post("/me/referral/inviter", json={"code": "abc"})).status_code == 401


async def test_card_shows_my_link(api: AsyncClient, db, redis) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)

    data = (await api.get("/me/referral", cookies=cookies)).json()["data"]

    assert data["code"] == user.handle
    assert data["link"].endswith(f"/?ref={user.handle}")
    assert (data["star_points"], data["invited"], data["can_add_inviter"]) == (0, 0, True)
    assert user.phone_e164 not in str(data)


async def test_inviter_is_rewarded_on_the_first_real_notch(
    api: AsyncClient,
    db,
    redis,
    bot_under_test,  # noqa: F811
) -> None:
    inviter, newcomer = await add_customer(db), await add_customer(db)
    cookies = await logged_in(api, db, redis, newcomer)

    response = await add_inviter(api, cookies, f"@{inviter.handle.upper()}")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["invited_by"] == inviter.handle
    assert await star_points(db, inviter) == 0  # nothing until a real Notch
    venue, secret = await add_venue(db)
    await receive(db, bot_under_test, newcomer.phone_e164, scan_text(venue, code_now(secret)))
    assert await star_points(db, inviter) == REFERRAL_STAR_POINTS
    # A second Notch rewards nothing more.
    other, other_secret = await add_venue(db, category="restaurant")
    await receive(db, bot_under_test, newcomer.phone_e164, scan_text(other, code_now(other_secret)))
    assert await star_points(db, inviter) == REFERRAL_STAR_POINTS
    card = (await api.get("/me/referral", cookies=await logged_in(api, db, redis, inviter))).json()
    assert (card["data"]["invited"], card["data"]["rewarded"]) == (1, 1)


async def test_already_notched_rewards_straight_away(api: AsyncClient, db, redis) -> None:
    inviter, newcomer = await add_customer(db), await add_customer(db)
    venue, _ = await add_venue(db)
    await ledger_visit(db, newcomer, venue, 12)
    cookies = await logged_in(api, db, redis, newcomer)

    await add_inviter(api, cookies, inviter.handle)

    assert await star_points(db, inviter) == REFERRAL_STAR_POINTS


async def test_only_one_inviter(api: AsyncClient, db, redis) -> None:
    first = await add_customer(db)
    second = await add_customer(db)
    newcomer = await add_customer(db)
    cookies = await logged_in(api, db, redis, newcomer)
    assert (await add_inviter(api, cookies, first.handle)).status_code == 200

    again = await add_inviter(api, cookies, second.handle)

    assert again.status_code == 409
    assert again.json()["error"]["code"] == "referrals.not_allowed"


@pytest.mark.parametrize("who", ["self", "unknown", "loop"])
async def test_refused_codes(api: AsyncClient, db, redis, who: str) -> None:
    user, friend = await add_customer(db), await add_customer(db)
    if who == "loop":
        db.add(Referral(referrer_id=user.id, referred_id=friend.id))
        await db.commit()
    code = {"self": user.handle, "unknown": "nobody.x9999", "loop": friend.handle}[who]
    cookies = await logged_in(api, db, redis, user)

    response = await add_inviter(api, cookies, code)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "referrals.code_invalid"


async def test_only_in_the_first_14_days(api: AsyncClient, db, redis) -> None:
    inviter, newcomer = await add_customer(db), await add_customer(db)
    newcomer.created_at = datetime.now(UTC) - timedelta(days=15)
    await db.commit()
    cookies = await logged_in(api, db, redis, newcomer)

    response = await add_inviter(api, cookies, inviter.handle)

    assert response.status_code == 409
    card = (await api.get("/me/referral", cookies=cookies)).json()["data"]
    assert card["can_add_inviter"] is False


async def test_monthly_cap(db) -> None:
    inviter = await add_customer(db)
    venue, _ = await add_venue(db)
    now = datetime.now(UTC)
    for _ in range(MAX_REWARDS_PER_MONTH + 1):
        newcomer = await add_customer(db)
        db.add(Referral(referrer_id=inviter.id, referred_id=newcomer.id))
        await db.commit()
        await ledger_visit(db, newcomer, venue, 12, at=now)
        await reward_first_notch(db, newcomer.id, now=now)
        await db.commit()

    assert await star_points(db, inviter) == MAX_REWARDS_PER_MONTH * REFERRAL_STAR_POINTS


async def test_deleting_the_inviter_deletes_their_star_points(db) -> None:
    inviter, newcomer = await add_customer(db), await add_customer(db)
    referral = Referral(referrer_id=inviter.id, referred_id=newcomer.id)
    db.add(referral)
    await db.flush()
    db.add(
        StarPointsEntry(user_id=inviter.id, referral_id=referral.id, reason="referral", points=50)
    )
    await db.commit()

    inviter_id = inviter.id
    await db.delete(inviter)
    await db.commit()

    left = await db.scalar(
        select(func.count())
        .select_from(StarPointsEntry)
        .where(StarPointsEntry.user_id == inviter_id)
    )
    rows = await db.scalar(
        select(func.count()).select_from(Referral).where(Referral.referred_id == newcomer.id)
    )
    assert (left, rows) == (0, 0)


async def test_two_notchs_at_once_reward_once(sessions, redis) -> None:  # noqa: F811
    """The newcomer's first two Notchs land at the same moment, on real connections."""
    async with sessions() as session:
        inviter, newcomer = User(phone_e164=phone()), User(phone_e164=phone())
        session.add_all([inviter, newcomer])
        await session.flush()
        session.add(Referral(referrer_id=inviter.id, referred_id=newcomer.id))
        await session.commit()

    async def reward() -> bool:
        async with sessions() as session:
            given = await reward_first_notch(session, newcomer.id, now=datetime.now(UTC))
            await session.commit()
            return given

    results = await asyncio.gather(reward(), reward())

    assert sorted(results) == [False, True]
    async with sessions() as session:
        assert await star_points(session, inviter) == REFERRAL_STAR_POINTS
