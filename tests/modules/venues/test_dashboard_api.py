"""The venue app over HTTP (E4, E5-E14)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fakeredis import FakeAsyncRedis
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.cookies import DEVICE_COOKIE
from app.modules.locations.time import Period
from app.modules.locations.time import today as local_today
from app.modules.venues.dashboard import (
    EN_DASH,
    bars_for,
    busiest_label,
    busiest_window,
    ordinal,
)
from app.modules.venues.founding import add_months
from tests import factories as f


@pytest.mark.parametrize(
    ("n", "text"),
    [
        (1, "1st"),
        (2, "2nd"),
        (3, "3rd"),
        (4, "4th"),
        (11, "11th"),
        (12, "12th"),
        (21, "21st"),
        (113, "113th"),
    ],
)
def test_ordinal(n: int, text: str) -> None:
    assert ordinal(n) == text


def test_busiest_window_and_label() -> None:
    assert busiest_window({18: 5, 19: 4, 12: 6}) == 18
    assert busiest_label(18) == f"6{EN_DASH}8 PM"
    assert busiest_label(11) == f"11 AM{EN_DASH}1 PM"
    assert busiest_label(23) == f"11 PM{EN_DASH}1 AM"
    assert busiest_window({}) is None


def test_bars_for_7_and_30_days() -> None:
    week = Period(date(2026, 10, 19), date(2026, 10, 25))
    bars = bars_for(week, {date(2026, 10, 20): 3})
    assert [b.label for b in bars] == list("MTWTFSS") and bars[1].notches == 3
    month = Period(date(2026, 9, 26), date(2026, 10, 25))
    bars = bars_for(month, {date(2026, 9, 26): 1, date(2026, 10, 25): 2})
    assert [b.label for b in bars] == ["W1", "W2", "W3", "W4", "W5"]
    assert (bars[0].start, bars[0].end, bars[0].notches) == (
        date(2026, 9, 26),
        date(2026, 9, 27),
        1,
    )
    assert bars[-1].notches == 2


def test_add_months() -> None:
    assert add_months(date(2026, 10, 14), 2) == date(2026, 12, 14)
    assert add_months(date(2026, 12, 31), 2) == date(2027, 2, 28)


async def _venue_with_visits(db: AsyncSession, redis: FakeAsyncRedis):
    city = await f.city(db)
    area = await f.area(db, city)
    venue = await f.venue(db, city, name="Kahwa House", area_row=area, founding_venue_no=7)
    counter = await f.counter(db, venue)
    owner = await f.member(db, venue)
    regular = await f.user(db)
    now = datetime.now(UTC)
    for days in range(3):
        await f.notch_at(db, redis, regular, venue, now - timedelta(days=days))
    await f.notch_at(db, redis, await f.user(db), venue, now)
    return venue, counter, owner


async def test_owner_home_and_customers(
    api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    _, _, owner = await _venue_with_visits(db, redis)
    api.cookies = f.login(owner)

    home = (await api.get("/venue/home")).json()["data"]
    assert home["unlocked"] is False and home["visits_so_far"] == 4 and home["unlock_at"] == 10
    assert home["customers"] == 2 and home["new_customers"] == 2
    assert home["headline"] == "2 customers this week · 2 new"
    assert len(home["bars"]) == 7 and sum(b["notches"] for b in home["bars"]) == 4
    assert home["counter"]["online"] is True
    assert len((await api.get("/venue/home?range=30d")).json()["data"]["bars"]) == 5

    customers = (await api.get("/venue/customers")).json()["data"]
    assert (customers["new"], customers["returning"]) == (2, 0)
    labels = {v["label"] for v in customers["today"]}
    assert "1st visit" in labels and ("3rd visit" in labels or len(labels) == 1)

    visits = (await api.get("/venue/visits")).json()["data"]
    assert visits["total"] == 2  # today's two

    today = local_today("Asia/Kolkata")  # the venue's day, not UTC's
    csv = await api.get(f"/venue/visits.csv?from={today - timedelta(days=5)}&to={today}")
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0] == "date,time,notch_no,visit_no,new_customer"
    assert len(csv.text.splitlines()) == 5
    assert "+91" not in csv.text

    days = (await api.get(f"/venue/daily?from={today - timedelta(days=2)}&to={today}")).json()
    assert len(days["data"]) == 3
    too_long = await api.get(f"/venue/daily?from={today - timedelta(days=200)}&to={today}")
    assert too_long.status_code == 400


async def test_profile_billing_certificate_and_cards(
    api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    _, _, owner = await _venue_with_visits(db, redis)
    api.cookies = f.login(owner)

    updated = await api.patch(
        "/venue/profile",
        json={
            "description": "Filter coffee and late-night chai.",
            "tags": ["late_night", "work_friendly"],
            "opening_hours": {"mon": [["09:00", "23:00"]]},
        },
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["tags"] == ["late_night", "work_friendly"]
    too_many = await api.patch(
        "/venue/profile", json={"tags": ["late_night", "outdoor", "family", "veg_only"]}
    )
    assert too_many.status_code == 422

    billing = (await api.get("/venue/billing")).json()["data"]
    assert billing["fee_paise"] is None  # no price until it is set per environment
    assert billing["verified_visits_this_cycle"] >= 1
    assert billing["payments_available"] is False

    certificate = (await api.get("/venue/certificate")).json()["data"]
    assert certificate["founding_venue_no"] == 7 and certificate["total"] == 30

    assert (await api.get("/venue/cards")).json()["data"] == []
    assert (await api.get("/venue/cards/2026-09")).json()["error"][
        "code"
    ] == "venues.card_not_ready"

    contact = await api.patch(
        "/venue/me/contact", json={"name": "Abhishek", "phone": "98765 00000"}
    )
    assert contact.json()["data"]["phone"] == "+919876500000"
    assert (await api.post("/venue/close-request", json={"reason": "Closing"})).status_code == 202


async def test_counter_display_gets_the_qr_message_and_today_count(
    api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    venue, counter, _ = await _venue_with_visits(db, redis)
    token = "device-token-for-tests"
    from app.modules.auth.tokens import hash_token

    counter.token_hash = hash_token(token)
    await db.flush()
    api.cookies.set(DEVICE_COOKIE, token, path="/api/v1/display")

    config = (await api.get("/display/config")).json()["data"]
    assert (
        config["qr"]["message_template"]
        == f"Notch me at Kahwa House · {{code}} [{venue.venue_code}]"
    )
    assert config["qr"]["whatsapp_number"] == "919099012345"
    assert config["notched_today"] == 2
    assert config["venue"]["area"] == "Navrangpura"

    beat = await api.post("/display/heartbeat", json={"battery_pct": 82, "is_charging": False})
    assert beat.status_code == 200 and beat.json()["data"]["notched_today"] == 2
    await db.refresh(counter)
    assert counter.battery_pct == 82 and counter.is_charging is False


async def test_null_tags_clear_them(
    api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    _, _, owner = await _venue_with_visits(db, redis)
    api.cookies = f.login(owner)

    cleared = await api.patch("/venue/profile", json={"tags": None})
    assert cleared.status_code == 200 and cleared.json()["data"]["tags"] == []
