"""The admin panel (`/admin/overview`, venues, Notchs, customers) on a real database."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.modules.admin_users.models import AdminRole
from app.modules.leaderboards.service import LeaderboardService, Period
from app.modules.referrals.models import (
    Referral,
    ReferralStatus,
    StarPointsEntry,
    StarPointsReason,
)
from app.modules.referrals.service import REFERRAL_STAR_POINTS, reward_first_notch
from app.modules.scans.models import VisitStatus
from app.modules.users.models import UserStatus
from app.modules.venues.models import CounterDevice, CounterStatus, VenueStatus
from tests.integration.test_admin import admin_cookies, make_admin  # noqa: F401  (fixture)
from tests.integration.test_leaderboards import ledger_visit, logged_in
from tests.integration.test_scan_bot import add_customer
from tests.integration.test_scan_pipeline import add_venue

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def status(api: AsyncClient, url: str, value: str, cookies: dict[str, str]) -> int:
    return (await api.patch(url, json={"status": value}, cookies=cookies)).status_code


async def test_everything_needs_an_admin_login(api: AsyncClient, db, redis) -> None:
    api.cookies.clear()
    paths = ["/admin/overview", "/admin/venues", "/admin/notchs", "/admin/customers"]
    for path in paths:
        assert (await api.get(path)).status_code == 401, path
    customer = await add_customer(db)
    cookies = await logged_in(api, db, redis, customer)
    assert (await api.get("/admin/customers", cookies=cookies)).status_code == 401


async def test_overview_counts_real_rows(api: AsyncClient, db, admin_cookies) -> None:  # noqa: F811
    venue, _ = await add_venue(db)
    user = await add_customer(db)
    await ledger_visit(db, user, venue, 12, at=datetime.now(UTC))

    data = (await api.get("/admin/overview", cookies=admin_cookies)).json()["data"]

    assert data["venues_live"] >= 1
    assert data["notchs_today"] >= 1
    assert data["customers"] >= 1


async def test_venues_list_with_counter_and_counts(
    api: AsyncClient,
    db,
    admin_cookies,  # noqa: F811
) -> None:
    venue, _ = await add_venue(db, name="Zebra Cafe Unique")
    await ledger_visit(db, await add_customer(db), venue, 12, at=datetime.now(UTC))

    response = await api.get(
        "/admin/venues", params={"q": "Zebra Cafe Unique"}, cookies=admin_cookies
    )

    rows = response.json()["data"]["items"]
    assert [(r["name"], r["counter_paired"], r["notchs_total"]) for r in rows] == [
        ("Zebra Cafe Unique", True, 1)
    ]


async def test_search_terms_are_not_patterns(api: AsyncClient, db, admin_cookies) -> None:  # noqa: F811
    await add_venue(db, name="Plain Name")

    response = await api.get("/admin/venues", params={"q": "%"}, cookies=admin_cookies)

    assert response.status_code == 200
    assert response.json()["data"]["total"] == 0


async def test_pause_resume_and_close_a_venue(
    api: AsyncClient,
    db,
    admin_cookies,  # noqa: F811
) -> None:
    venue, _ = await add_venue(db)
    url = f"/admin/venues/{venue.venue_code}/status"

    assert await status(api, url, "paused", admin_cookies) == 200
    await db.refresh(venue)
    assert venue.status == VenueStatus.PAUSED
    # A double click changes nothing.
    assert await status(api, url, "paused", admin_cookies) == 200
    assert await status(api, url, "counter_live", admin_cookies) == 200
    assert await status(api, url, "closed", admin_cookies) == 200

    reopen = await api.patch(url, json={"status": "counter_live"}, cookies=admin_cookies)
    assert reopen.status_code == 409
    assert reopen.json()["error"]["code"] == "admin.status_change_not_allowed"


async def test_resuming_needs_a_paired_counter(
    api: AsyncClient,
    db,
    admin_cookies,  # noqa: F811
) -> None:
    venue, _ = await add_venue(db)
    venue.status = VenueStatus.PAUSED
    counter = await db.scalar(select(CounterDevice).where(CounterDevice.venue_id == venue.id))
    counter.token_hash, counter.status = None, CounterStatus.PENDING_PAIRING
    await db.commit()

    response = await api.patch(
        f"/admin/venues/{venue.venue_code}/status",
        json={"status": "counter_live"},
        cookies=admin_cookies,
    )

    assert response.json()["error"]["code"] == "admin.counter_not_paired"


async def test_reviewers_cannot_change_status(api: AsyncClient, db, redis) -> None:
    reviewer = await make_admin(db, redis, api, role=AdminRole.REVIEWER)
    venue, _ = await add_venue(db)

    response = await api.patch(
        f"/admin/venues/{venue.venue_code}/status", json={"status": "paused"}, cookies=reviewer
    )

    assert response.status_code == 403


async def test_bad_status_and_unknown_venue(api: AsyncClient, admin_cookies) -> None:  # noqa: F811
    bad = await api.patch(
        "/admin/venues/VEN-AAAAAAAA/status", json={"status": "pending"}, cookies=admin_cookies
    )
    unknown = await api.patch(
        "/admin/venues/VEN-AAAAAAAA/status", json={"status": "paused"}, cookies=admin_cookies
    )

    assert bad.status_code == 422
    assert unknown.status_code == 404


async def test_revoke_a_notch_takes_its_points_away(
    api: AsyncClient,
    db,
    redis,
    admin_cookies,  # noqa: F811
) -> None:
    venue, _ = await add_venue(db)
    user = await add_customer(db)
    visit = await ledger_visit(db, user, venue, 12, at=datetime.now(UTC))
    service = LeaderboardService(redis)
    await service.add_points(venue.city_id, user.id, 12, visit.visit_date)
    url = f"/admin/venues/{venue.venue_code}/notchs/{visit.notch_no}/revoke"

    first = await api.post(url, json={"reason": "Same code from a photo"}, cookies=admin_cookies)
    again = await api.post(url, json={"reason": "Same code from a photo"}, cookies=admin_cookies)

    assert first.status_code == 200, first.text
    assert first.json()["data"]["status"] == "revoked"
    assert again.status_code == 200  # twice changes nothing
    await db.refresh(visit)
    assert visit.status == VisitStatus.REVOKED
    assert visit.revoked_reason == "Same code from a photo"
    assert visit.revoked_at is not None and visit.revoked_by_admin_id is not None
    rank = await service.rank_of(venue.city_id, user.id, Period.ALL)
    assert rank is None or rank.points == 0
    cookies = await logged_in(api, db, redis, user)
    me = (await api.get("/me", cookies=cookies)).json()["data"]
    assert me["totals"] == {"notches": 0, "places": 0, "points": 0}


async def test_revoke_unknown_notch(api: AsyncClient, db, admin_cookies) -> None:  # noqa: F811
    venue, _ = await add_venue(db)

    response = await api.post(
        f"/admin/venues/{venue.venue_code}/notchs/999/revoke",
        json={"reason": "test"},
        cookies=admin_cookies,
    )

    assert response.status_code == 404


async def test_notchs_list_filters_by_venue(api: AsyncClient, db, admin_cookies) -> None:  # noqa: F811
    venue, _ = await add_venue(db)
    user = await add_customer(db)
    await ledger_visit(db, user, venue, 12, at=datetime.now(UTC))

    data = (
        await api.get(
            "/admin/notchs", params={"venue_code": venue.venue_code}, cookies=admin_cookies
        )
    ).json()["data"]

    assert data["total"] == 1
    row = data["items"][0]
    assert (row["customer_handle"], row["points"], row["status"]) == (user.handle, 12, "valid")
    assert user.phone_e164 not in str(data)


async def test_customers_show_only_the_last_four_digits(
    api: AsyncClient,
    db,
    admin_cookies,  # noqa: F811
) -> None:
    user = await add_customer(db)

    response = await api.get("/admin/customers", params={"q": user.handle}, cookies=admin_cookies)

    rows = response.json()["data"]["items"]
    assert [(r["handle"], r["phone_last4"]) for r in rows] == [
        (user.handle, f"…{user.phone_e164[-4:]}")
    ]
    assert user.phone_e164 not in response.text
    assert user.phone_e164[3:] not in response.text


async def test_block_and_unblock_a_customer(
    api: AsyncClient,
    db,
    admin_cookies,  # noqa: F811
) -> None:
    user = await add_customer(db)
    url = f"/admin/customers/{user.handle}/status"

    blocked = await api.patch(
        url, json={"status": "blocked", "reason": "Fake bill claims"}, cookies=admin_cookies
    )
    assert blocked.status_code == 200
    await db.refresh(user)
    assert user.status == UserStatus.BLOCKED
    assert (user.blocked_reason, user.blocked_at is not None) == ("Fake bill claims", True)
    assert await status(api, url, "active", admin_cookies) == 200
    await db.refresh(user)
    assert user.blocked_reason is None and user.blocked_at is None
    assert await status(api, url, "blocked", admin_cookies) == 200  # no reason given
    await db.refresh(user)
    assert user.blocked_reason == "No reason given"
    assert await status(api, url, "active", admin_cookies) == 200
    deleted = await api.patch(url, json={"status": "deleted"}, cookies=admin_cookies)
    assert deleted.status_code == 422


async def test_a_deleted_customer_cannot_be_unblocked(
    api: AsyncClient,
    db,
    admin_cookies,  # noqa: F811
) -> None:
    user = await add_customer(db, status=UserStatus.DELETED)

    response = await api.patch(
        f"/admin/customers/{user.handle}/status", json={"status": "active"}, cookies=admin_cookies
    )

    assert response.status_code == 404


async def test_passport_points_skip_revoked_notchs(api: AsyncClient, db, redis) -> None:
    venue, _ = await add_venue(db)
    user = await add_customer(db)
    kept = await ledger_visit(db, user, venue, 12, at=datetime(2026, 11, 1, 6, tzinfo=UTC))
    other, _ = await add_venue(db)
    removed = await ledger_visit(db, user, other, 10, at=datetime(2026, 11, 2, 6, tzinfo=UTC))
    removed.status, removed.revoked_reason = VisitStatus.REVOKED, "Fraud (test)"
    await db.commit()
    cookies = await logged_in(api, db, redis, user)

    totals = (await api.get("/me", cookies=cookies)).json()["data"]["totals"]

    assert kept.status == VisitStatus.VALID
    assert totals == {"notches": 1, "places": 1, "points": 12}


async def test_removing_a_notch_takes_back_the_invite_reward_it_paid_for(
    api: AsyncClient,
    db,
    redis,
    admin_cookies,  # noqa: F811
) -> None:
    """Team decision 12 Nov 2026: a fake Notch never pays out Star Points."""
    venue, _ = await add_venue(db)
    inviter, newcomer = await add_customer(db), await add_customer(db)
    db.add(Referral(referrer_id=inviter.id, referred_id=newcomer.id, source="link"))
    await db.commit()
    visit = await ledger_visit(db, newcomer, venue, 12, at=datetime.now(UTC))
    assert await reward_first_notch(db, newcomer.id, now=datetime.now(UTC), visit_id=visit.id)
    await db.commit()
    referral = await db.scalar(select(Referral).where(Referral.referred_id == newcomer.id))
    assert (referral.status, referral.qualifying_visit_id) == (ReferralStatus.REWARDED, visit.id)

    url = f"/admin/venues/{venue.venue_code}/notchs/{visit.notch_no}/revoke"
    response = await api.post(url, json={"reason": "Code shared online"}, cookies=admin_cookies)

    assert response.status_code == 200, response.text
    await db.refresh(referral)
    assert referral.status == ReferralStatus.CANCELLED
    rows = (
        await db.scalars(select(StarPointsEntry).where(StarPointsEntry.user_id == inviter.id))
    ).all()
    assert sorted((r.reason, r.points) for r in rows) == [
        (StarPointsReason.REFERRAL, REFERRAL_STAR_POINTS),
        (StarPointsReason.REFERRAL_REVERSED, -REFERRAL_STAR_POINTS),
    ]
    again = await api.post(url, json={"reason": "Code shared online"}, cookies=admin_cookies)
    assert again.status_code == 200  # twice takes nothing more
    count = await db.scalar(
        select(func.count())
        .select_from(StarPointsEntry)
        .where(StarPointsEntry.user_id == inviter.id)
    )
    assert count == 2
