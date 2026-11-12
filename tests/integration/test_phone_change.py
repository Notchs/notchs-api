"""Moving a customer to a new WhatsApp number: a code from the website, sent from the new phone."""

import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.modules.users.models import User, UserStatus
from app.modules.users.phone_change import (
    MAX_CODES_PER_WINDOW,
    MAX_TRIES_PER_WINDOW,
    PhoneChangeOutcome,
    PhoneChangeService,
    parse_command,
)
from app.modules.whatsapp.bot import PHONE_CHANGE_REPLIES
from tests.integration.test_leaderboards import ledger_visit, logged_in
from tests.integration.test_scan_bot import (
    add_customer,
    bot_under_test,  # noqa: F401  (fixture)
    meta,  # noqa: F401  (fixture)
    receive,
)
from tests.integration.test_scan_pipeline import add_venue, phone
from tests.integration.test_scan_races import sessions  # noqa: F401  (fixture)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def code_for(api: AsyncClient, cookies: dict[str, str]) -> str:
    response = await api.post("/me/phone-change", cookies=cookies)
    assert response.status_code == 200, response.text
    return response.json()["data"]["message"]  # "CHANGE 123456"


async def users_with(db, number: str) -> int:
    return await db.scalar(select(func.count()).select_from(User).where(User.phone_e164 == number))


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("CHANGE 123456", "123456"),
        ("  change 123456 ", "123456"),
        ("CHANGE 12345", None),
        ("CHANGE 123456 please", None),
        ("Hey Notchs!, get me notched. KAHWA-123456", None),
        (None, None),
    ],
)
def test_parse_command(text, code) -> None:
    assert parse_command(text) == code


async def test_endpoint_needs_a_login(api: AsyncClient) -> None:
    api.cookies.clear()
    assert (await api.post("/me/phone-change")).status_code == 401


async def test_account_moves_to_the_new_number(
    api: AsyncClient,
    db,
    redis,
    bot_under_test,  # noqa: F811
    meta,  # noqa: F811
) -> None:
    venue, _ = await add_venue(db)
    user = await add_customer(db)
    await ledger_visit(db, user, venue, 12)
    old, new = user.phone_e164, phone()
    cookies = await logged_in(api, db, redis, user)
    text = await code_for(api, cookies)

    await receive(db, bot_under_test, new, text)

    await db.refresh(user)
    assert user.phone_e164 == new
    assert await users_with(db, old) == 0
    assert await users_with(db, new) == 1  # no second account was made for the new number
    assert meta.texts()[-1] == PHONE_CHANGE_REPLIES[PhoneChangeOutcome.CHANGED]
    me = (await api.get("/me", cookies=cookies)).json()["data"]  # still logged in
    assert me["totals"]["notches"] == 1


async def test_a_code_works_once(
    api: AsyncClient,
    db,
    redis,
    bot_under_test,  # noqa: F811
    meta,  # noqa: F811
) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)
    text = await code_for(api, cookies)
    first = phone()
    await receive(db, bot_under_test, first, text)
    await redis.flushdb()  # no reply limit in the way

    await receive(db, bot_under_test, phone(), text)

    await db.refresh(user)
    assert user.phone_e164 == first
    assert meta.texts()[-1] == PHONE_CHANGE_REPLIES[PhoneChangeOutcome.INVALID]


async def test_a_new_code_cancels_the_old_one(api: AsyncClient, db, redis) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)
    old = (await code_for(api, cookies)).split()[1]
    new = (await code_for(api, cookies)).split()[1]
    service = PhoneChangeService(db, redis)

    assert (await service.finish(phone(), old))[0] is PhoneChangeOutcome.INVALID
    if old != new:
        assert (await service.finish(phone(), new))[0] is PhoneChangeOutcome.CHANGED


async def test_a_number_with_its_own_account_is_refused(
    api: AsyncClient,
    db,
    redis,
    bot_under_test,  # noqa: F811
    meta,  # noqa: F811
) -> None:
    user, other = await add_customer(db), await add_customer(db)
    old = user.phone_e164
    cookies = await logged_in(api, db, redis, user)
    text = await code_for(api, cookies)

    await receive(db, bot_under_test, other.phone_e164, text)

    await db.refresh(user)
    assert user.phone_e164 == old
    assert meta.texts()[-1] == PHONE_CHANGE_REPLIES[PhoneChangeOutcome.TAKEN]


async def test_sending_from_the_same_number(
    api: AsyncClient,
    db,
    redis,
    bot_under_test,  # noqa: F811
    meta,  # noqa: F811
) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)

    await receive(db, bot_under_test, user.phone_e164, await code_for(api, cookies))

    assert meta.texts()[-1] == PHONE_CHANGE_REPLIES[PhoneChangeOutcome.SAME]


async def test_wrong_codes_are_limited_per_number(db, redis) -> None:
    sender = phone()
    service = PhoneChangeService(db, redis)
    for _ in range(MAX_TRIES_PER_WINDOW):
        assert (await service.finish(sender, "000000"))[0] is PhoneChangeOutcome.INVALID

    assert (await service.finish(sender, "000000"))[0] is PhoneChangeOutcome.LIMITED


async def test_codes_are_limited_per_customer(api: AsyncClient, db, redis) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)
    for _ in range(MAX_CODES_PER_WINDOW):
        await code_for(api, cookies)

    response = await api.post("/me/phone-change", cookies=cookies)

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "passport.phone_change_rate_limited"


async def test_a_blocked_customer_cannot_move(api: AsyncClient, db, redis) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)
    code = (await code_for(api, cookies)).split()[1]
    user.status, user.blocked_reason = UserStatus.BLOCKED, "Fake bills (test)"
    await db.commit()

    outcome, _ = await PhoneChangeService(db, redis).finish(phone(), code)

    assert outcome is PhoneChangeOutcome.INVALID


async def test_wrong_code_from_a_stranger_creates_no_account(
    db,
    redis,
    bot_under_test,  # noqa: F811
    meta,  # noqa: F811
) -> None:
    stranger = phone()

    await receive(db, bot_under_test, stranger, "CHANGE 000000")

    assert await users_with(db, stranger) == 0
    assert meta.texts()[-1] == PHONE_CHANGE_REPLIES[PhoneChangeOutcome.INVALID]


async def test_the_same_code_sent_twice_at_once_moves_once(sessions, redis) -> None:  # noqa: F811
    """Two phones send one code at the same moment, on connections that really commit."""
    async with sessions() as session:
        user = User(phone_e164=phone())
        session.add(user)
        await session.commit()
        started = await PhoneChangeService(session, redis).start(user)
    assert started is not None

    async def attempt(number: str) -> PhoneChangeOutcome:
        async with sessions() as session:
            outcome, _ = await PhoneChangeService(session, redis).finish(number, started.code)
            await session.commit()
            return outcome

    results = await asyncio.gather(attempt(phone()), attempt(phone()))

    assert sorted(results) == sorted([PhoneChangeOutcome.CHANGED, PhoneChangeOutcome.INVALID])
