"""The "apply as a venue" form on a real database: code, verify, submit, and every rule."""

import re
import uuid

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.locations.models import City
from app.modules.venue_applications.models import ApplicationStatus, VenueApplication
from app.modules.venue_applications.service import MAX_APPLICATIONS_PER_IP_PER_DAY
from app.modules.venues.models import Venue, VenueMember

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def city(db: AsyncSession) -> City:
    row = City(
        name="Ahmedabad", slug=f"ahd-{uuid.uuid4().hex[:6]}", state="Gujarat", is_active=True
    )
    db.add(row)
    await db.commit()
    return row


@pytest.fixture
def email() -> str:
    return f"owner-{uuid.uuid4().hex[:6]}@kahwa.in"


async def send_code(api: AsyncClient, email: str) -> Response:
    return await api.post(
        "/venue-applications/email/send-code", json={"email": email, "turnstile_token": "ok"}
    )


def code_in(outbox) -> str:
    match = re.search(r"\b(\d{6})\b", outbox.sent[-1].text)
    assert match, outbox.sent[-1].text
    return match[1]


async def verified_token(api: AsyncClient, outbox, email: str) -> str:
    assert (await send_code(api, email)).status_code == 202
    response = await api.post(
        "/venue-applications/email/verify", json={"email": email, "code": code_in(outbox)}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["verification_token"]


def form(token: str, city: City, **overrides: object) -> dict[str, object]:
    return {
        "verification_token": token,
        "venue_name": "Kahwa House",
        "category": "cafe",
        "city_id": str(city.id),
        "google_maps_url": "https://maps.app.goo.gl/AbCdEf123",
        "contact_name": "Riya Mehta",
        "phone": "98765 43210",
    } | overrides


async def submit(api: AsyncClient, body: dict[str, object]) -> Response:
    return await api.post("/venue-applications", json=body)


async def test_full_flow(
    api: AsyncClient, db: AsyncSession, outbox, city: City, email: str
) -> None:
    token = await verified_token(api, outbox, email)

    response = await submit(
        api,
        form(
            token,
            city,
            landmark="Opposite Law Garden",
            latitude="23.022505",
            longitude="72.571362",
            notes="Open till 1 am",
        ),
    )

    assert response.status_code == 201, response.text
    code = response.json()["data"]["application_code"]
    assert code.startswith("APP-")
    row = await db.scalar(select(VenueApplication).where(VenueApplication.application_code == code))
    assert (row.email, row.phone_e164, row.status) == (
        email,
        "+919876543210",
        ApplicationStatus.PENDING,
    )
    assert (row.landmark, str(row.latitude), row.notes) == (
        "Opposite Law Garden",
        "23.022505",
        "Open till 1 am",
    )
    assert row.email_verified_at is not None
    assert outbox.sent[0].subject.startswith("Your Notchs code:")


async def test_email_case_does_not_matter(api: AsyncClient, outbox, city: City) -> None:
    await send_code(api, "Owner@Kahwa.IN")
    verify = await api.post(
        "/venue-applications/email/verify",
        json={"email": "owner@kahwa.in", "code": code_in(outbox)},
    )

    assert verify.status_code == 200
    assert outbox.sent[-1].to == "owner@kahwa.in"


async def test_wrong_code(api: AsyncClient, outbox, email: str) -> None:
    await send_code(api, email)
    wrong = "000000" if code_in(outbox) != "000000" else "111111"

    response = await api.post(
        "/venue-applications/email/verify", json={"email": email, "code": wrong}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "auth.code_invalid"


async def test_resend_needs_a_minute(api: AsyncClient, email: str) -> None:
    await send_code(api, email)

    again = await send_code(api, email)

    assert again.status_code == 429
    assert again.json()["error"]["code"] == "auth.rate_limited"


async def test_send_code_needs_the_captcha(api: AsyncClient, turnstile, outbox, email: str) -> None:
    turnstile.passes = False

    response = await send_code(api, email)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "captcha.failed"
    assert outbox.sent == []


async def test_disposable_email_refused(api: AsyncClient, outbox) -> None:
    response = await send_code(api, "someone@mailinator.com")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "auth.email_not_allowed"
    assert outbox.sent == []


async def test_submit_without_verification(api: AsyncClient, city: City) -> None:
    response = await submit(api, form("x" * 43, city))

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "applications.email_not_verified"


async def test_token_is_used_up_by_a_successful_submit(
    api: AsyncClient, outbox, city: City, email: str
) -> None:
    token = await verified_token(api, outbox, email)

    assert (await submit(api, form(token, city))).status_code == 201
    assert (await submit(api, form(token, city, venue_name="Again"))).status_code == 400


async def test_a_rejected_submit_keeps_the_token(
    api: AsyncClient, db: AsyncSession, outbox, city: City, email: str
) -> None:
    token = await verified_token(api, outbox, email)
    closed = City(name="Surat", slug=f"srt-{uuid.uuid4().hex[:6]}", state="Gujarat")
    db.add(closed)
    await db.commit()

    refused = await submit(api, form(token, closed))
    fixed = await submit(api, form(token, city))

    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "applications.city_invalid"
    assert fixed.status_code == 201


async def test_unknown_city(api: AsyncClient, outbox, city: City, email: str) -> None:
    token = await verified_token(api, outbox, email)

    response = await submit(api, form(token, city, city_id=str(uuid.uuid4())))

    assert response.status_code == 422


async def test_one_pending_application_per_email(
    api: AsyncClient, db: AsyncSession, redis, outbox, city: City, email: str
) -> None:
    first = await verified_token(api, outbox, email)
    assert (await submit(api, form(first, city))).status_code == 201

    await redis.delete(f"otp:cd:application:{email}")  # skip the resend minute
    second = await verified_token(api, outbox, email)
    duplicate = await submit(api, form(second, city, venue_name="Kahwa 2"))

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "applications.duplicate"


async def test_rejected_applicants_may_apply_again(
    api: AsyncClient, db: AsyncSession, redis, outbox, city: City, email: str
) -> None:
    first = await verified_token(api, outbox, email)
    code = (await submit(api, form(first, city))).json()["data"]["application_code"]
    row = await db.scalar(select(VenueApplication).where(VenueApplication.application_code == code))
    row.status = ApplicationStatus.REJECTED
    await db.commit()

    await redis.delete(f"otp:cd:application:{email}")
    second = await verified_token(api, outbox, email)

    assert (await submit(api, form(second, city))).status_code == 201


async def test_existing_venue_login_cannot_apply(
    api: AsyncClient, db: AsyncSession, outbox, city: City, email: str
) -> None:
    venue = Venue(city_id=city.id, name="Kahwa", slug=f"k-{uuid.uuid4().hex[:6]}", category="cafe")
    db.add(venue)
    await db.flush()
    db.add(VenueMember(venue_id=venue.id, phone_e164="+919800000001", name="R", email=email))
    await db.commit()
    token = await verified_token(api, outbox, email)  # the code is still sent: no enumeration

    response = await submit(api, form(token, city))

    assert response.status_code == 409


async def test_daily_limit_per_ip(api: AsyncClient, redis, outbox, city: City) -> None:
    for i in range(MAX_APPLICATIONS_PER_IP_PER_DAY):
        token = await verified_token(api, outbox, f"owner{i}-{uuid.uuid4().hex[:4]}@kahwa.in")
        assert (await submit(api, form(token, city))).status_code == 201
    await redis.delete(*[k async for k in redis.scan_iter("rl:otp:*")])  # only the apply limit left
    token = await verified_token(api, outbox, f"late-{uuid.uuid4().hex[:4]}@kahwa.in")

    response = await submit(api, form(token, city))

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "applications.rate_limited"


async def test_team_is_notified_when_configured(
    api: AsyncClient, outbox, city: City, email: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ADMIN_NOTIFY_EMAIL", "team@notchs.com")
    token = await verified_token(api, outbox, email)

    await submit(api, form(token, city))

    notice = outbox.sent[-1]
    assert notice.to == "team@notchs.com"
    assert "Kahwa House" in notice.subject and "Ahmedabad" in notice.subject
    assert email not in notice.text and "98765" not in notice.text  # no applicant contact details


async def test_no_team_email_when_not_configured(
    api: AsyncClient, outbox, city: City, email: str
) -> None:
    token = await verified_token(api, outbox, email)

    await submit(api, form(token, city))

    assert [m.subject.startswith("Your Notchs code") for m in outbox.sent] == [True]
