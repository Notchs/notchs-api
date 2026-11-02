"""Venue scan codes on a real database: made on approval, unique, and handed to the counter phone
so it can draw the scan QR."""

import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.locations.models import City
from app.modules.venues.models import Venue
from tests.integration.test_admin import make_admin, make_application
from tests.integration.test_counter import cookie, display_config, pair, pairing_token
from tests.integration.test_counter import owner as counter_owner  # noqa: F401 (a fixture)

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def city(db: AsyncSession) -> City:
    row = City(name="Ahmedabad", slug=f"ahd-{uuid.uuid4().hex[:6]}", state="Gujarat")
    db.add(row)
    await db.commit()
    return row


async def test_approval_gives_the_venue_a_scan_code(
    api: AsyncClient, db: AsyncSession, redis, city: City
) -> None:
    cookies = await make_admin(db, redis, api)
    first = await make_application(db, city, venue_name="Kahwa House")
    second = await make_application(db, city, venue_name="Kahwa Corner")

    for application in (first, second):
        response = await api.post(
            f"/admin/venue-applications/{application.id}/approve", json={}, cookies=cookies
        )
        assert response.status_code == 200, response.text

    codes = (
        await db.scalars(
            select(Venue.scan_code)
            .where(Venue.name.in_(["Kahwa House", "Kahwa Corner"]), Venue.city_id == city.id)
            .order_by(Venue.created_at)
        )
    ).all()
    assert len(codes) == 2 and len(set(codes)) == 2
    assert all(code.startswith("KAHWA") for code in codes)


async def test_the_database_refuses_duplicate_or_malformed_codes(db: AsyncSession, city) -> None:
    db.add(Venue(city_id=city.id, name="A", slug="a", category="cafe", scan_code="DUPE"))
    await db.flush()

    for bad in ("DUPE", "lower", "HAS-HYPHEN"):
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                db.add(
                    Venue(
                        city_id=city.id,
                        name="B",
                        slug=f"b-{uuid.uuid4().hex[:4]}",
                        category="cafe",
                        scan_code=bad,
                    )
                )
                await db.flush()


async def test_venues_made_without_a_code_still_get_one(db: AsyncSession, city) -> None:
    venue = Venue(city_id=city.id, name="Script venue", slug="script", category="cafe")
    db.add(venue)
    await db.flush()

    ok = await db.scalar(
        text("SELECT scan_code ~ '^[A-Z][A-Z0-9]{2,9}$' FROM venues WHERE id = :id"),
        {"id": venue.id},
    )
    assert ok is True


# --- the counter phone ----------------------------------------------------------------------------


async def test_counter_phone_gets_the_qr_settings(
    api: AsyncClient,
    db: AsyncSession,
    counter_owner,  # noqa: F811 (the fixture)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "WHATSAPP_PUBLIC_NUMBER", "15556755530")
    venue = await db.get(Venue, counter_owner["venue_id"])
    paired = await pair(api, await pairing_token(api, counter_owner))

    response = await display_config(api, cookie(paired, "nx_device"))

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["venue"]["scan_code"] == venue.scan_code
    scan = data["scan"]
    assert scan["whatsapp_number"] == "15556755530"
    assert scan["message"] == "Hey Notchs!, get me notched."
    assert scan["text_example"] == f"Hey Notchs!, get me notched. {venue.scan_code}-482913"
    link = scan["link_template"].replace("{code}", "123456")
    assert parse_qs(urlsplit(link).query)["text"][0] == (
        f"Hey Notchs!, get me notched. {venue.scan_code}-123456"
    )


async def test_no_qr_until_the_number_is_set(
    api: AsyncClient,
    counter_owner,  # noqa: F811 (the fixture)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "WHATSAPP_PUBLIC_NUMBER", None)
    paired = await pair(api, await pairing_token(api, counter_owner))

    response = await display_config(api, cookie(paired, "nx_device"))

    assert response.status_code == 200
    assert response.json()["data"]["scan"] is None
