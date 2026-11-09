"""Deleting a customer's account: personal data goes, visits stay anonymous (team decision)."""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.modules.admin_users.models import AdminRole
from app.modules.auth.models import AuthToken
from app.modules.claims.models import VisitClaim
from app.modules.leaderboards.service import ALL_TIME, board_key
from app.modules.referrals.models import Referral, StarPointsEntry, StarPointsReason
from app.modules.scans.models import PointsEntry, Visit
from app.modules.users.models import User, UserStatus
from app.modules.whatsapp.models import WhatsAppMessage
from tests.integration.test_admin import make_admin
from tests.integration.test_leaderboards import logged_in
from tests.integration.test_scan_bot import (
    add_customer,
    bot_under_test,  # noqa: F401  (fixture)
    code_now,
    meta,  # noqa: F401  (fixture)
    receive,
    scan_text,
)
from tests.integration.test_scan_pipeline import add_venue

pytestmark = pytest.mark.asyncio(loop_scope="session")

CONFIRM = {"confirm": "DELETE"}


async def count(db, model, *where) -> int:
    return await db.scalar(select(func.count()).select_from(model).where(*where)) or 0


async def fresh(db, user: User | uuid.UUID) -> User:
    """Read again (an error response rolls the test session back, expiring loaded rows)."""
    user_id = user if isinstance(user, uuid.UUID) else user.id
    found = await db.scalar(
        select(User).where(User.id == user_id).execution_options(populate_existing=True)
    )
    assert found is not None
    return found


async def notched_customer(db, redis, bot) -> tuple[User, str]:
    """A customer with a Notch, an Instagram username and a claim. Returns them and their
    number."""
    venue, secret = await add_venue(db)
    await db.commit()
    user = await add_customer(db)
    phone = user.phone_e164
    await receive(db, bot, phone, scan_text(venue, code_now(secret)))
    user = await fresh(db, user)
    user.instagram_handle = "riya.eats"
    user.show_instagram_to_venues = True
    db.add(VisitClaim(user_id=user.id, venue_id=venue.id, visit_date=datetime.now(UTC).date()))
    await db.commit()
    return user, phone


async def test_needs_a_login_and_the_word(api: AsyncClient, db, redis) -> None:
    api.cookies.clear()
    assert (await api.post("/me/delete", json=CONFIRM)).status_code == 401
    user = await add_customer(db)
    user_id = user.id
    cookies = await logged_in(api, db, redis, user)

    for body in ({}, {"confirm": "delete"}, {"confirm": "yes"}):
        assert (await api.post("/me/delete", json=body, cookies=cookies)).status_code == 422
    assert (await fresh(db, user_id)).deleted_at is None


async def test_delete_my_account(
    api: AsyncClient,
    db,
    redis,
    bot_under_test,  # noqa: F811
) -> None:
    user, phone = await notched_customer(db, redis, bot_under_test)
    inviter, friend = await add_customer(db), await add_customer(db)
    invited_me = Referral(
        referrer_id=inviter.id, referred_id=user.id, rewarded_at=datetime.now(UTC)
    )
    i_invited = Referral(referrer_id=user.id, referred_id=friend.id)
    db.add_all([invited_me, i_invited])
    await db.flush()
    db.add_all(
        [
            StarPointsEntry(
                user_id=inviter.id,
                referral_id=invited_me.id,
                reason=StarPointsReason.REFERRAL,
                points=50,
            ),
            StarPointsEntry(user_id=user.id, reason=StarPointsReason.ADJUSTMENT, points=5),
        ]
    )
    await db.commit()
    city_id = (await fresh(db, user)).city_id
    assert city_id is not None  # set by their first scan
    board = board_key(city_id, ALL_TIME)
    await redis.zadd(board, {str(user.id): 12})
    user_id, handle, inviter_id = user.id, user.handle, inviter.id
    referral_ids = [invited_me.id, i_invited.id]
    cookies = await logged_in(api, db, redis, user)

    response = await api.post("/me/delete", json=CONFIRM, cookies=cookies)

    assert response.status_code == 200, response.text
    assert "nx_user_at" in response.headers.get("set-cookie", "")
    assert (await api.get("/me", cookies=cookies)).status_code in (401, 403)
    gone = await fresh(db, user_id)
    assert gone.status == UserStatus.DELETED and gone.deleted_at is not None
    assert (gone.phone_e164, gone.name, gone.instagram_handle, gone.consent_at) == (
        None,
        None,
        None,
        None,
    )
    assert gone.show_instagram_to_venues is False
    assert gone.handle != handle
    # Nothing left that says who they were.
    assert await count(db, WhatsAppMessage, WhatsAppMessage.phone_e164 == phone) == 0
    scrubbed = (
        await db.scalars(select(WhatsAppMessage).where(WhatsAppMessage.user_id == user_id))
    ).all()
    assert scrubbed and all(m.body is None and m.payload is None for m in scrubbed)
    assert await count(db, AuthToken, AuthToken.user_id == user_id) == 0
    assert await count(db, VisitClaim, VisitClaim.user_id == user_id) == 0
    assert await count(db, Referral, Referral.id.in_(referral_ids)) == 0
    assert await count(db, StarPointsEntry, StarPointsEntry.user_id == user_id) == 0
    # Their inviter keeps the Star Points they earned.
    assert await count(db, StarPointsEntry, StarPointsEntry.user_id == inviter_id) == 1
    # The visit stays, anonymous, so the venue's totals don't change; it's off the board.
    assert await count(db, Visit, Visit.user_id == user_id) == 1
    assert await count(db, PointsEntry, PointsEntry.user_id == user_id) > 0
    assert await redis.zscore(board, str(user_id)) is None
    # Doing it twice is harmless... but the login is gone.
    again = await api.post("/me/delete", json=CONFIRM, cookies=cookies)
    assert again.status_code in (401, 403)


async def test_the_same_number_starts_afresh(
    api: AsyncClient,
    db,
    redis,
    bot_under_test,  # noqa: F811
) -> None:
    user, phone = await notched_customer(db, redis, bot_under_test)
    user_id = user.id
    await api.post("/me/delete", json=CONFIRM, cookies=await logged_in(api, db, redis, user))

    await receive(db, bot_under_test, phone, "LOGIN")

    new = await db.scalar(select(User).where(User.phone_e164 == phone))
    assert new is not None and new.id != user_id
    assert await count(db, Visit, Visit.user_id == new.id) == 0


async def test_admin_deletes_on_request(
    api: AsyncClient,
    db,
    redis,
    bot_under_test,  # noqa: F811
) -> None:
    user, _ = await notched_customer(db, redis, bot_under_test)
    user_id, handle = user.id, user.handle
    reviewer = await make_admin(db, redis, api, AdminRole.REVIEWER)
    admin = await make_admin(db, redis, api)
    path = f"/admin/customers/{handle}/delete"

    by_reviewer = await api.post(path, json={"confirm": handle}, cookies=reviewer)
    wrong = await api.post(path, json={"confirm": "someone.else"}, cookies=admin)
    done = await api.post(path, json={"confirm": f"@{handle.upper()}"}, cookies=admin)
    twice = await api.post(path, json={"confirm": handle}, cookies=admin)

    assert by_reviewer.status_code == 403
    assert wrong.status_code == 422
    assert done.status_code == 200, done.text
    assert twice.status_code == 404
    assert (await fresh(db, user_id)).status == UserStatus.DELETED
    customers = await api.get("/admin/customers", params={"q": handle}, cookies=admin)
    assert customers.json()["data"]["items"] == []
    notchs = (await api.get("/admin/notchs", params={"size": 100}, cookies=admin)).json()
    visit = await db.scalar(select(Visit).where(Visit.user_id == user_id))
    assert visit is not None
    rows = [r for r in notchs["data"]["items"] if r["notch_no"] == visit.notch_no]
    assert rows and all(r["customer_handle"] != handle for r in rows)
