"""Step 4 on a real database: admin login and roles, the review queue, approve/reject, seed, CLI."""

import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.seed import CITIES, seed
from app.modules.admin_users.models import AdminRole, AdminUser
from app.modules.auth.admin_auth import AdminAuthService
from app.modules.locations.models import Area, City
from app.modules.venue_applications.models import ApplicationStatus, VenueApplication
from app.modules.venues.models import Venue, VenueMember, VenueStatus

pytestmark = pytest.mark.asyncio(loop_scope="session")

ADMIN_PASSWORD = "admin password 2026"


def token_from(text: str, app_url: str) -> str:
    link = re.search(r"https?://\S+", text)
    assert link and link[0].startswith(f"{app_url}/set-password?token="), text
    return parse_qs(urlsplit(link[0]).query)["token"][0]


def cookie(response: Response, name: str) -> str:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"{name} not set")


async def make_admin(
    db: AsyncSession, redis, api: AsyncClient, role: AdminRole = AdminRole.ADMIN
) -> dict[str, str]:
    """An admin with a password, logged in. Returns the admin access cookie."""
    admin = AdminUser(name="Abhishek", email=f"a-{uuid.uuid4().hex[:6]}@notchs.com", role=role)
    db.add(admin)
    await db.flush()
    invite = await AdminAuthService(db, redis).invite_email(admin)
    await db.commit()
    response = await api.post(
        "/admin/auth/set-password",
        json={"token": token_from(invite.text, settings.ADMIN_APP_URL), "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"nx_admin_at": cookie(response, "nx_admin_at")}


@pytest.fixture
async def admin_cookies(db, redis, api) -> dict[str, str]:
    return await make_admin(db, redis, api)


@pytest.fixture
async def city(db: AsyncSession) -> City:
    row = City(
        name="Ahmedabad", slug=f"ahd-{uuid.uuid4().hex[:6]}", state="Gujarat", is_active=True
    )
    db.add(row)
    await db.flush()
    db.add(Area(city_id=row.id, name="Navrangpura", slug="navrangpura"))
    await db.commit()
    return row


@pytest.fixture
async def area(db: AsyncSession, city: City) -> Area:
    return await db.scalar(select(Area).where(Area.city_id == city.id))


async def make_application(db: AsyncSession, city: City, **overrides: object) -> VenueApplication:
    data: dict[str, object] = {
        "venue_name": "Kahwa House",
        "category": "cafe",
        "city_id": city.id,
        "google_maps_url": "https://maps.app.goo.gl/AbCdEf123",
        "landmark": "Opposite Law Garden",
        "contact_name": "Riya Mehta",
        "email": f"owner-{uuid.uuid4().hex[:6]}@kahwa.in",
        "email_verified_at": datetime.now(UTC),
        "phone_e164": "+919876543210",
    }
    row = VenueApplication(**(data | overrides))
    db.add(row)
    await db.commit()
    return row


# --- admin login ------------------------------------------------------------------------


async def test_admin_set_password_me_login_refresh(db, redis, api: AsyncClient) -> None:
    cookies = await make_admin(db, redis, api)

    me = await api.get("/admin/me", cookies=cookies)
    assert me.status_code == 200
    data = me.json()["data"]
    assert data["role"] == "admin" and data["admin_user_code"].startswith("ADM-")

    login = await api.post(
        "/admin/auth/login", json={"email": data["email"], "password": ADMIN_PASSWORD}
    )
    assert login.status_code == 200
    rt = cookie(login, "nx_admin_rt")
    assert (await api.post("/auth/admin/refresh", cookies={"nx_admin_rt": rt})).status_code == 200


async def test_venue_cookie_is_not_an_admin_cookie(api: AsyncClient, admin_cookies) -> None:
    api.cookies.clear()  # the client keeps the admin cookie from logging in

    response = await api.get("/admin/me", cookies={"nx_venue_at": admin_cookies["nx_admin_at"]})

    assert response.status_code == 401


async def test_admin_forgot_password_link_points_at_the_admin_app(
    db, redis, api: AsyncClient, outbox, admin_cookies
) -> None:
    email = (await api.get("/admin/me", cookies=admin_cookies)).json()["data"]["email"]

    await api.post("/admin/auth/forgot-password", json={"email": email, "turnstile_token": "ok"})

    assert token_from(outbox.sent[-1].text, settings.ADMIN_APP_URL)


# --- review queue ---------------------------------------------------------------------------


async def test_queue_needs_an_admin_login(api: AsyncClient) -> None:
    assert (await api.get("/admin/venue-applications")).status_code == 401


async def test_queue_lists_pending_oldest_first(
    db, api: AsyncClient, admin_cookies, city: City
) -> None:
    first = await make_application(db, city, venue_name="First")
    second = await make_application(db, city, venue_name="Second")
    await make_application(db, city, venue_name="Old one", status=ApplicationStatus.REJECTED)

    page = (await api.get("/admin/venue-applications", cookies=admin_cookies)).json()["data"]
    codes = [item["application_code"] for item in page["items"]]

    assert codes.index(first.application_code) < codes.index(second.application_code)
    assert all(item["status"] == "pending" for item in page["items"])
    rejected = await api.get(
        "/admin/venue-applications", params={"status": "rejected"}, cookies=admin_cookies
    )
    assert "Old one" in [i["venue_name"] for i in rejected.json()["data"]["items"]]


async def test_get_by_id_or_code(db, api: AsyncClient, admin_cookies, city: City) -> None:
    application = await make_application(db, city)

    by_id = await api.get(f"/admin/venue-applications/{application.id}", cookies=admin_cookies)
    by_code = await api.get(
        f"/admin/venue-applications/{application.application_code.lower()}", cookies=admin_cookies
    )

    assert by_id.json()["data"]["id"] == by_code.json()["data"]["id"] == str(application.id)
    assert by_id.json()["data"]["google_maps_url"].startswith("https://maps.app.goo.gl/")


@pytest.mark.parametrize("ref", ["APP-00000000", str(uuid.uuid4()), "nonsense"])
async def test_unknown_application(api: AsyncClient, admin_cookies, ref: str) -> None:
    response = await api.get(f"/admin/venue-applications/{ref}", cookies=admin_cookies)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "applications.not_found"


# --- approve ----------------------------------------------------------------------------


async def approve(api: AsyncClient, cookies, ref: object, **body: object) -> Response:
    return await api.post(f"/admin/venue-applications/{ref}/approve", json=body, cookies=cookies)


async def test_approve_end_to_end(
    db, api: AsyncClient, outbox, admin_cookies, city: City, area: Area
) -> None:
    application = await make_application(
        db, city, latitude=Decimal("23.022505"), longitude=Decimal("72.571362")
    )
    admin_id = (await api.get("/admin/me", cookies=admin_cookies)).json()["data"]["id"]
    email = application.email

    response = await approve(
        api,
        admin_cookies,
        application.application_code,
        area_id=str(area.id),
        address="12 C G Road, Navrangpura",
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["venue_code"].startswith("VEN-") and data["venue_slug"] == "kahwa-house"
    assert data["application"]["status"] == "approved"
    venue = await db.get(Venue, uuid.UUID(data["venue_id"]))
    await db.refresh(venue)
    assert (venue.status, str(venue.signed_by_admin_id)) == (VenueStatus.SIGNED, admin_id)
    assert (venue.area_id, venue.address, venue.landmark) == (
        area.id,
        "12 C G Road, Navrangpura",
        "Opposite Law Garden",
    )
    assert str(venue.latitude) == "23.022505"  # the applicant's location by default
    member = await db.scalar(select(VenueMember).where(VenueMember.email == email))
    assert (member.name, member.phone_e164, member.role) == ("Riya Mehta", "+919876543210", "owner")

    # The owner gets the welcome email, sets a password and logs in to the venue app.
    welcome = outbox.sent[-1]
    assert welcome.to == email and "Kahwa House" in welcome.subject
    token = token_from(welcome.text, settings.VENUE_APP_URL)
    first = await api.post(
        "/venue/auth/set-password", json={"token": token, "password": "owner pass 1984"}
    )
    assert first.status_code == 200
    me = await api.get("/venue/me", cookies={"nx_venue_at": cookie(first, "nx_venue_at")})
    assert me.json()["data"]["venue"]["venue_code"] == data["venue_code"]


async def test_admin_location_overrides_the_applicant(
    db, api: AsyncClient, admin_cookies, city: City
) -> None:
    application = await make_application(
        db, city, latitude=Decimal("23.1"), longitude=Decimal("72.1")
    )

    response = await approve(
        api, admin_cookies, application.id, latitude="23.022505", longitude="72.571362"
    )

    venue = await db.get(Venue, uuid.UUID(response.json()["data"]["venue_id"]))
    assert str(venue.latitude) == "23.022505"


async def test_cannot_review_twice(db, api: AsyncClient, admin_cookies, city: City) -> None:
    application_id = (await make_application(db, city)).id
    assert (await approve(api, admin_cookies, application_id)).status_code == 200

    again = await approve(api, admin_cookies, application_id)
    reject = await api.post(
        f"/admin/venue-applications/{application_id}/reject", json={}, cookies=admin_cookies
    )

    assert again.status_code == reject.status_code == 409
    assert again.json()["error"]["code"] == "applications.invalid_state"


async def test_area_must_be_in_the_city(db, api: AsyncClient, admin_cookies, city: City) -> None:
    other = City(name="Surat", slug=f"srt-{uuid.uuid4().hex[:6]}", state="Gujarat")
    db.add(other)
    await db.flush()
    far = Area(city_id=other.id, name="Adajan", slug="adajan")
    db.add(far)
    await db.commit()
    application = await make_application(db, city)

    response = await approve(api, admin_cookies, application.id, area_id=str(far.id))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "applications.area_invalid"


async def test_email_already_a_venue_login(db, api: AsyncClient, admin_cookies, city: City) -> None:
    taken = await make_application(db, city)
    assert (await approve(api, admin_cookies, taken.id)).status_code == 200
    # A second application with the same email (the first is approved, so this one is allowed).
    again = await make_application(db, city, email=taken.email, venue_name="Kahwa 2")

    response = await approve(api, admin_cookies, again.id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "applications.duplicate"


async def test_reviewer_can_view_but_not_decide(db, redis, api: AsyncClient, city: City) -> None:
    reviewer = await make_admin(db, redis, api, AdminRole.REVIEWER)
    application = await make_application(db, city)

    listed = await api.get("/admin/venue-applications", cookies=reviewer)
    approved = await approve(api, reviewer, application.id)

    assert listed.status_code == 200
    assert approved.status_code == 403
    assert approved.json()["error"]["code"] == "auth.admin_role_required"


# --- reject -----------------------------------------------------------------------------


async def test_reject_and_tell_the_applicant(
    db, api: AsyncClient, outbox, admin_cookies, city: City
) -> None:
    application = await make_application(db, city)
    email = application.email

    response = await api.post(
        f"/admin/venue-applications/{application.id}/reject",
        json={"reason": "We're not in your area yet."},
        cookies=admin_cookies,
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "rejected"
    assert response.json()["data"]["rejection_reason"] == "We're not in your area yet."
    assert outbox.sent[-1].to == email
    assert "We're not in your area yet." in outbox.sent[-1].text


async def test_reject_quietly(db, api: AsyncClient, outbox, admin_cookies, city: City) -> None:
    application = await make_application(db, city)
    before = len(outbox.sent)

    await api.post(
        f"/admin/venue-applications/{application.id}/reject",
        json={"notify": False},
        cookies=admin_cookies,
    )

    assert len(outbox.sent) == before


# --- seed and CLI ---------------------------------------------------------------------------


async def test_seed_is_idempotent(db: AsyncSession) -> None:
    await seed(db)
    await seed(db)
    await db.commit()

    seeded = CITIES[0]
    city = await db.scalar(select(City).where(City.slug == seeded.slug))
    areas = await db.scalar(select(func.count()).select_from(Area).where(Area.city_id == city.id))
    assert city.is_active and city.state == "Gujarat"
    assert areas == len(seeded.areas)


def run_cli(db_schema: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        env=os.environ | {"DB_SCHEMA": db_schema, "EMAIL_DRY_RUN": "true"},
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_cli_create_admin_and_new_link(db_schema: str) -> None:
    email = f"cli-{uuid.uuid4().hex[:6]}@notchs.com"

    created = run_cli(db_schema, "create-admin", "--email", email, "--name", "CLI Admin")
    duplicate = run_cli(db_schema, "create-admin", "--email", email, "--name", "Again")
    relinked = run_cli(db_schema, "admin-link", "--email", email)
    unknown = run_cli(db_schema, "admin-link", "--email", "nobody@notchs.com")

    assert created.returncode == 0, created.stdout + created.stderr
    assert (
        "ADM-" in created.stdout
        and f"{settings.ADMIN_APP_URL}/set-password?token=" in created.stdout
    )
    assert duplicate.returncode == 1 and "already exists" in duplicate.stdout
    assert relinked.returncode == 0 and "set-password?token=" in relinked.stdout
    assert unknown.returncode == 1
