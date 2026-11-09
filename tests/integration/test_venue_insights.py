"""A venue's dashboard numbers (`/venue/insights/*`) on a real database. Only its own visits, and
guests only as numbers."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scans.models import ScanSession, VisitStatus
from app.modules.users.models import User
from app.modules.venue_insights.schemas import Segment
from app.modules.venue_insights.service import _months_back, segment_of
from app.modules.venues.models import Venue
from tests.integration.test_counter import owner  # noqa: F401  (fixture)
from tests.integration.test_leaderboards import ledger_visit
from tests.integration.test_scan_bot import add_customer, store
from tests.integration.test_scan_pipeline import add_venue

pytestmark = pytest.mark.asyncio(loop_scope="session")


def ago(**delta: float) -> datetime:
    return datetime.now(UTC) - timedelta(**delta)


async def visit(db: AsyncSession, user: User, venue: Venue, at: datetime, visit_no: int = 1):
    row = await ledger_visit(db, user, venue, 12, at=at)
    row.visit_no_at_venue = visit_no
    await db.commit()
    return row


async def refused(db: AsyncSession, user: User, venue: Venue, reason: str) -> None:
    message = await store(db, user.phone_e164, "x")
    db.add(
        ScanSession(
            user_id=user.id,
            venue_id=venue.id,
            inbound_message_id=message.id,
            status="rejected",
            reject_reason=reason,
            code_submitted="123456",
        )
    )
    await db.commit()


async def test_everything_needs_a_venue_login(api: AsyncClient) -> None:
    api.cookies.clear()
    for path in ("overview", "scans", "guests"):
        assert (await api.get(f"/venue/insights/{path}")).status_code == 401


async def test_a_customer_login_is_not_a_venue_login(api: AsyncClient, db, redis) -> None:
    from tests.integration.test_leaderboards import logged_in

    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)

    assert (await api.get("/venue/insights/guests", cookies=cookies)).status_code == 401


async def test_overview_counts_this_range_against_the_last(
    api: AsyncClient,
    db,
    owner,  # noqa: F811
) -> None:
    venue = await db.get(Venue, owner["venue_id"])
    regular, newcomer = await add_customer(db), await add_customer(db)
    await visit(db, regular, venue, ago(days=10))  # the week before
    await visit(db, regular, venue, ago(hours=30), visit_no=2)
    await visit(db, newcomer, venue, ago(hours=1))

    response = await api.get("/venue/insights/overview", cookies=owner["cookies"])

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    activity = data["activity"]
    assert (activity["visits"], activity["visits_previous"]) == (2, 1)
    assert (activity["new_guests"], activity["new_guests_previous"]) == (1, 1)
    assert activity["unique_guests"] == 2
    assert (activity["returning_rate"], activity["returning_rate_previous"]) == (50, 0)
    assert len(activity["days"]) == 7
    assert len(activity["previous_days"]) == 7
    assert sum(day["visits"] for day in activity["previous_days"]) == 1
    assert activity["previous_days"][-1]["day"] < activity["days"][0]["day"]
    assert sum(day["visits"] for day in activity["days"]) == 2
    assert data["stage"] == "first_days"
    assert data["first_days"] == {"verified_visits": 3, "target": 10}
    assert data["counter"]["paired"] is False


async def test_an_empty_venue_has_no_returning_rate(
    api: AsyncClient,
    owner,  # noqa: F811
) -> None:
    data = (await api.get("/venue/insights/overview?range=30d", cookies=owner["cookies"])).json()[
        "data"
    ]

    assert data["activity"]["returning_rate"] is None
    assert len(data["activity"]["days"]) == 30
    assert data["recent_scans"] == []


async def test_only_this_venues_visits(api: AsyncClient, db, owner) -> None:  # noqa: F811
    other, _ = await add_venue(db)
    user = await add_customer(db)
    await visit(db, user, other, ago(hours=2))

    data = (await api.get("/venue/insights/overview", cookies=owner["cookies"])).json()["data"]
    guests = (await api.get("/venue/insights/guests", cookies=owner["cookies"])).json()["data"]

    assert data["activity"]["visits"] == 0
    assert guests["summary"]["guests"] == 0


async def test_revoked_visits_do_not_count(api: AsyncClient, db, owner) -> None:  # noqa: F811
    venue = await db.get(Venue, owner["venue_id"])
    row = await visit(db, await add_customer(db), venue, ago(hours=2))
    row.status = VisitStatus.REVOKED
    await db.commit()

    data = (await api.get("/venue/insights/overview", cookies=owner["cookies"])).json()["data"]

    assert data["activity"]["visits"] == 0


async def test_guests_are_numbers_never_people(api: AsyncClient, db, owner) -> None:  # noqa: F811
    venue = await db.get(Venue, owner["venue_id"])
    first, second = await add_customer(db), await add_customer(db)
    first.name, second.name = "Riya Mehta", "Asha Shah"
    for n in range(1, 6):
        await visit(db, first, venue, ago(days=20 - n), visit_no=n)
    await visit(db, second, venue, ago(hours=3))

    response = await api.get("/venue/insights/guests", cookies=owner["cookies"])

    data = response.json()["data"]
    assert data["summary"] == {"guests": 2, "new": 1, "returning": 0, "regulars": 1, "at_risk": 0}
    assert [(g["guest_no"], g["segment"], g["total_visits"]) for g in data["guests"]] == [
        (2, "new", 1),
        (1, "regular", 5),
    ]
    assert data["guests"][1]["average_gap_days"] == 1.0
    assert data["guests"][1]["days_to_second_visit"] == 1.0
    assert data["guests"][0]["days_to_second_visit"] is None
    assert len(data["month_labels"]) == 6 and len(data["guests"][0]["visits_by_month"]) == 6
    for secret in (first.phone_e164, second.phone_e164, "Riya", "Asha", first.handle):
        assert secret not in response.text


async def test_instagram_only_when_the_customer_chose_to_show_it(
    api: AsyncClient,
    db,
    owner,  # noqa: F811
) -> None:
    venue = await db.get(Venue, owner["venue_id"])
    shows, hides = await add_customer(db), await add_customer(db)
    shows.instagram_handle, shows.show_instagram_to_venues = "riya.eats", True
    hides.instagram_handle = "asha.eats"
    await db.commit()
    await visit(db, shows, venue, ago(hours=2))
    await visit(db, hides, venue, ago(hours=1))

    response = await api.get("/venue/insights/guests", cookies=owner["cookies"])

    handles = {g["guest_no"]: g["instagram_handle"] for g in response.json()["data"]["guests"]}
    assert handles == {1: "riya.eats", 2: None}
    assert "asha.eats" not in response.text


async def test_guest_numbers_stay_with_the_person(
    api: AsyncClient,
    db,
    owner,  # noqa: F811
) -> None:
    venue = await db.get(Venue, owner["venue_id"])
    first, second = await add_customer(db), await add_customer(db)
    revoked = await visit(db, first, venue, ago(days=3))
    revoked.status = VisitStatus.REVOKED
    await visit(db, second, venue, ago(days=2))
    await visit(db, first, venue, ago(days=1), visit_no=1)

    data = (await api.get("/venue/insights/guests", cookies=owner["cookies"])).json()["data"]

    # The first person keeps #1 although their first Notch was revoked.
    assert [g["guest_no"] for g in data["guests"]] == [1, 2]


async def test_scans_funnel_and_reasons(api: AsyncClient, db, owner) -> None:  # noqa: F811
    venue = await db.get(Venue, owner["venue_id"])
    guest, stranger = await add_customer(db), await add_customer(db)
    await visit(db, guest, venue, ago(minutes=30))
    await refused(db, guest, venue, "already_today")
    await refused(db, stranger, venue, "expired_code")
    await refused(db, stranger, venue, "expired_code")

    response = await api.get("/venue/insights/scans", cookies=owner["cookies"])

    data = response.json()["data"]
    assert data["funnel"] == {
        "attempts": 4,
        "verified": 1,
        "refused": {"already_today": 1, "expired_code": 2},
    }
    outcomes = sorted((s["outcome"], s["guest_no"]) for s in data["scans"])
    assert outcomes == [
        ("already_today", 1),
        ("expired_code", None),
        ("expired_code", None),
        ("verified", 1),
    ]
    assert stranger.phone_e164 not in response.text


async def test_bad_range_is_refused(api: AsyncClient, owner) -> None:  # noqa: F811
    response = await api.get("/venue/insights/overview?range=90d", cookies=owner["cookies"])

    assert response.status_code == 422


def test_segments() -> None:
    now = datetime(2026, 11, 15, tzinfo=UTC)
    assert segment_of(1, now, now) is Segment.NEW
    assert segment_of(1, now - timedelta(days=60), now) is Segment.NEW
    assert segment_of(3, now, now) is Segment.RETURNING
    assert segment_of(5, now, now) is Segment.REGULAR
    assert segment_of(5, now - timedelta(days=31), now) is Segment.AT_RISK


def test_month_labels_cross_the_year() -> None:
    from datetime import date

    assert _months_back(date(2027, 2, 10), 4) == ["2026-11", "2026-12", "2027-01", "2027-02"]
