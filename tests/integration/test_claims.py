"""Bill-photo claims: starting one on the website, the photo on WhatsApp, and the admin's review."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.clients.whatsapp import Media, WhatsAppMediaError, get_whatsapp_client
from app.core.config import settings
from app.main import app
from app.modules.admin_users.models import AdminRole
from app.modules.claims.models import ClaimStatus, VisitClaim
from app.modules.claims.router import get_claim_service
from app.modules.claims.service import (
    ClaimService,
    end_of_day,
    parse_command,
    photo_media_id,
)
from app.modules.leaderboards.service import ALL_TIME, board_key
from app.modules.scans.models import PointsEntry, ScanSession, ScanSource, Visit
from app.modules.users.models import User
from app.modules.venues.models import Venue, VenueStatus
from app.modules.whatsapp.bot import (
    CLAIM_IN_REVIEW,
    CLAIM_NO_CLAIM,
    CLAIM_PHOTO_RECEIVED,
    CLAIM_SEND_PHOTO,
    CLAIM_UNKNOWN,
    WhatsAppBot,
)
from app.modules.whatsapp.models import WhatsAppMessage
from tests.integration.test_admin import make_admin
from tests.integration.test_leaderboards import logged_in
from tests.integration.test_scan_bot import (
    FakeMeta,
    add_customer,
    bot_under_test,  # noqa: F401  (fixture)
    code_now,
    meta,  # noqa: F401  (fixture)
    receive,
    scan_text,
)
from tests.integration.test_scan_pipeline import add_venue

pytestmark = pytest.mark.asyncio(loop_scope="session")

PHOTO = Media(b"\xff\xd8\xff\xe0bill", "image/jpeg")
GONE_MEDIA_ID = "999000999"


@pytest.fixture
def overrides(db, redis, meta: FakeMeta, monkeypatch):  # noqa: F811
    """WhatsApp stand-ins: photos come from memory and messages go to `meta`."""

    async def fetch(media_id: str) -> Media:
        if media_id == GONE_MEDIA_ID:
            raise WhatsAppMediaError("gone")
        return PHOTO

    monkeypatch.setattr(settings, "WHATSAPP_PUBLIC_NUMBER", "15550001111")
    app.dependency_overrides[get_claim_service] = lambda: ClaimService(db, redis, fetch)
    app.dependency_overrides[get_whatsapp_client] = lambda: meta
    yield
    app.dependency_overrides.pop(get_claim_service, None)
    app.dependency_overrides.pop(get_whatsapp_client, None)


async def live_venue(
    db, name: str | None = None, status: str | None = None, **fields: Any
) -> tuple[Venue, str]:
    venue, secret = await add_venue(db, **fields)
    venue.name = name or venue.name
    venue.status = status or venue.status
    await db.commit()
    return venue, secret


async def start(api: AsyncClient, cookies, venue: Venue | str):
    """`venue` or its code (an error response rolls the test session back, expiring venues)."""
    code = venue if isinstance(venue, str) else venue.venue_code
    return await api.post("/me/claims", json={"venue_code": code}, cookies=cookies)


async def send_photo(
    db, bot: WhatsAppBot, sender: str, *, media_id: str = "1234567890", caption: str | None = None
) -> WhatsAppMessage:
    image: dict[str, Any] = {"id": media_id, "mime_type": "image/jpeg"}
    if caption is not None:
        image["caption"] = caption
    message = WhatsAppMessage(
        wa_message_id=f"wamid.{uuid.uuid4().hex}",
        direction="in",
        phone_e164=sender,
        message_type="image",
        body=caption,
        payload={"message": {"type": "image", "image": image}},
        status="received",
    )
    db.add(message)
    await db.flush()
    await bot(db, message)
    await db.commit()
    return message


async def claim_in_review(api, db, redis, bot, user: User, venue: Venue) -> str:
    """A claim with its photo, waiting for an admin."""
    cookies = await logged_in(api, db, redis, user)
    code = (await start(api, cookies, venue)).json()["data"]["claim_code"]
    await receive(db, bot, user.phone_e164, f"CLAIM {code}")
    await send_photo(db, bot, user.phone_e164)
    return code


async def claim_row(db, code: str) -> VisitClaim:
    claim = await db.scalar(
        select(VisitClaim)
        .where(VisitClaim.claim_code == code)
        .execution_options(populate_existing=True)
    )
    assert claim is not None
    return claim


# --- pure helpers -------------------------------------------------------------------------------


def test_parse_command() -> None:
    assert parse_command("CLAIM CLM-7K3M9QX2") == "CLM-7K3M9QX2"
    assert parse_command("  claim clm-7k3m9qx2 ") == "CLM-7K3M9QX2"
    assert parse_command("CLAIM CLM-7K3M") is None
    assert parse_command("I want to CLAIM CLM-7K3M9QX2") is None
    assert parse_command(None) is None


def test_photo_media_id_is_checked() -> None:
    def message(media_id: object) -> WhatsAppMessage:
        return WhatsAppMessage(payload={"message": {"image": {"id": media_id}}})

    assert photo_media_id(message("1234567890")) == "1234567890"
    assert photo_media_id(message("../../me")) is None
    assert photo_media_id(message(123)) is None
    assert photo_media_id(WhatsAppMessage(payload=None)) is None


def test_a_claim_ends_at_midnight_in_india() -> None:
    day = datetime(2026, 11, 9, tzinfo=UTC).date()
    assert end_of_day(day) == datetime(2026, 11, 9, 18, 30, tzinfo=UTC)


# --- the customer, on the website ---------------------------------------------------------------


async def test_needs_a_login(api: AsyncClient) -> None:
    api.cookies.clear()
    assert (await api.get("/me/claims")).status_code == 401
    assert (await api.get("/me/claims/venues", params={"q": "ka"})).status_code == 401
    assert (await api.post("/me/claims", json={"venue_code": "VEN-7K3M9QX2"})).status_code == 401
    assert (await api.get("/admin/claims")).status_code == 401


async def test_find_only_live_places(api: AsyncClient, db, redis) -> None:
    tag = uuid.uuid4().hex[:6]
    live, _ = await live_venue(db, name=f"Kahwa {tag}")
    await live_venue(db, name=f"Paused {tag}", status=VenueStatus.PAUSED)
    cookies = await logged_in(api, db, redis, await add_customer(db))

    response = await api.get("/me/claims/venues", params={"q": tag}, cookies=cookies)

    assert response.status_code == 200, response.text
    assert response.json()["data"] == [
        {"venue_code": live.venue_code, "name": f"Kahwa {tag}", "area": None, "niche": "café"}
    ]
    # LIKE's own characters are taken literally.
    response = await api.get("/me/claims/venues", params={"q": "%%"}, cookies=cookies)
    assert response.json()["data"] == []


async def test_start_a_claim(api: AsyncClient, db, redis, overrides) -> None:
    venue, _ = await live_venue(db)
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)

    response = await start(api, cookies, venue)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    code = data["claim_code"]
    assert data["message"] == f"CLAIM {code}"
    assert data["whatsapp_link"] == f"https://wa.me/15550001111?text=CLAIM%20{code}"
    # Starting again for the same place the same day gives the same claim.
    assert (await start(api, cookies, venue)).json()["data"]["claim_code"] == code
    mine = (await api.get("/me/claims", cookies=cookies)).json()["data"]
    assert [(c["claim_code"], c["status"]) for c in mine] == [(code, "awaiting_photo")]


async def test_no_claim_where_already_notched_today(
    api: AsyncClient,
    db,
    redis,
    bot_under_test,  # noqa: F811
) -> None:
    venue, secret = await live_venue(db)
    other_cafe, _ = await live_venue(db)
    restaurant, _ = await live_venue(db, category="restaurant")
    codes = [v.venue_code for v in (venue, other_cafe, restaurant)]
    user = await add_customer(db)
    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))
    cookies = await logged_in(api, db, redis, user)

    here = await start(api, cookies, codes[0])
    same_kind = await start(api, cookies, codes[1])
    other_kind = await start(api, cookies, codes[2])

    assert here.json()["error"]["code"] == "claims.already_notched"
    assert same_kind.status_code == 409
    assert same_kind.json()["error"]["code"] == "claims.niche_limit"
    assert other_kind.status_code == 200


async def test_only_live_places_and_three_a_day(api: AsyncClient, db, redis) -> None:
    paused = (await live_venue(db, status=VenueStatus.PAUSED))[0].venue_code
    places = [
        (await live_venue(db, category=c))[0].venue_code for c in ("cafe", "restaurant", "turf_gym")
    ]
    fourth = (await live_venue(db, category="other"))[0].venue_code
    cookies = await logged_in(api, db, redis, await add_customer(db))

    assert (await start(api, cookies, paused)).status_code == 404
    for venue in places:
        assert (await start(api, cookies, venue)).status_code == 200
    response = await start(api, cookies, fourth)

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "claims.limit"


# --- the customer, on WhatsApp ------------------------------------------------------------------


async def test_code_then_photo(
    api: AsyncClient,
    db,
    redis,
    meta: FakeMeta,  # noqa: F811
    bot_under_test,  # noqa: F811
) -> None:
    venue, _ = await live_venue(db)
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)
    code = (await start(api, cookies, venue)).json()["data"]["claim_code"]

    await receive(db, bot_under_test, user.phone_e164, f"claim {code.lower()}")
    photo = await send_photo(db, bot_under_test, user.phone_e164)
    await receive(db, bot_under_test, user.phone_e164, f"CLAIM {code}")

    assert meta.texts() == [
        CLAIM_SEND_PHOTO.format(venue=venue.name),
        CLAIM_PHOTO_RECEIVED.format(venue=venue.name),
        CLAIM_IN_REVIEW.format(venue=venue.name),
    ]
    claim = await claim_row(db, code)
    assert claim.status == ClaimStatus.IN_REVIEW
    assert (claim.photo_message_id, claim.photo_media_id) == (photo.id, "1234567890")
    mine = (await api.get("/me/claims", cookies=cookies)).json()["data"]
    assert mine[0]["status"] == "in_review"


async def test_a_photo_without_a_claim(
    db,
    meta: FakeMeta,  # noqa: F811
    bot_under_test,  # noqa: F811
) -> None:
    user = await add_customer(db)

    await send_photo(db, bot_under_test, user.phone_e164)
    await send_photo(db, bot_under_test, user.phone_e164)  # no second notice

    assert meta.texts() == [CLAIM_NO_CLAIM]


async def test_someone_elses_code_does_nothing(
    api: AsyncClient,
    db,
    redis,
    meta: FakeMeta,  # noqa: F811
    bot_under_test,  # noqa: F811
) -> None:
    venue, _ = await live_venue(db)
    owner, stranger = await add_customer(db), await add_customer(db)
    code = (await start(api, await logged_in(api, db, redis, owner), venue)).json()["data"][
        "claim_code"
    ]

    await receive(db, bot_under_test, stranger.phone_e164, f"CLAIM {code}")
    await send_photo(db, bot_under_test, stranger.phone_e164, caption=f"CLAIM {code}")

    assert meta.texts() == [CLAIM_UNKNOWN, CLAIM_NO_CLAIM]
    assert (await claim_row(db, code)).status == ClaimStatus.AWAITING_PHOTO


async def test_yesterdays_claim_takes_no_photo(
    api: AsyncClient,
    db,
    redis,
    meta: FakeMeta,  # noqa: F811
    bot_under_test,  # noqa: F811
) -> None:
    venue, _ = await live_venue(db)
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)
    code = (await start(api, cookies, venue)).json()["data"]["claim_code"]
    await db.execute(
        update(VisitClaim)
        .where(VisitClaim.claim_code == code)
        .values(visit_date=VisitClaim.visit_date - 1)
    )
    await db.commit()

    await send_photo(db, bot_under_test, user.phone_e164)

    assert meta.texts() == [CLAIM_NO_CLAIM]
    mine = (await api.get("/me/claims", cookies=cookies)).json()["data"]
    assert mine[0]["status"] == "expired"


# --- the admin ----------------------------------------------------------------------------------


async def test_queue_and_photo(
    api: AsyncClient,
    db,
    redis,
    overrides,
    bot_under_test,  # noqa: F811
) -> None:
    venue, _ = await live_venue(db)
    user = await add_customer(db)
    code = await claim_in_review(api, db, redis, bot_under_test, user, venue)
    admin = await make_admin(db, redis, api, AdminRole.REVIEWER)

    queue = (await api.get("/admin/claims", params={"size": 100}, cookies=admin)).json()["data"]
    photo = await api.get(f"/admin/claims/{code}/photo", cookies=admin)

    item = next(c for c in queue["items"] if c["claim_code"] == code)
    assert item["customer_handle"] == user.handle
    assert (item["status"], item["photo_available"], item["niche"]) == ("in_review", True, "café")
    assert item["counter_last_seen_at"] is None
    assert user.phone_e164 not in str(queue)
    assert photo.status_code == 200
    assert photo.content == PHOTO.content
    assert photo.headers["content-type"] == "image/jpeg"
    assert photo.headers["cache-control"] == "private, no-store"


async def test_photo_gone_from_whatsapp(
    api: AsyncClient,
    db,
    redis,
    overrides,
    bot_under_test,  # noqa: F811
) -> None:
    venue, _ = await live_venue(db)
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)
    code = (await start(api, cookies, venue)).json()["data"]["claim_code"]
    await send_photo(db, bot_under_test, user.phone_e164, media_id=GONE_MEDIA_ID)
    admin = await make_admin(db, redis, api)

    gone = await api.get(f"/admin/claims/{code}/photo", cookies=admin)
    await db.execute(
        update(VisitClaim)
        .where(VisitClaim.claim_code == code)
        .values(photo_received_at=datetime.now(UTC) - timedelta(days=40))
    )
    await db.commit()
    old = await api.get(f"/admin/claims/{code}/photo", cookies=admin)

    assert (gone.status_code, old.status_code) == (410, 410)
    assert (await api.get(f"/admin/claims/{code}", cookies=admin)).json()["data"][
        "photo_available"
    ] is False


async def test_approve_adds_a_verified_notch(
    api: AsyncClient,
    db,
    redis,
    overrides,
    meta: FakeMeta,  # noqa: F811
    bot_under_test,  # noqa: F811
) -> None:
    venue, _ = await live_venue(db)
    user = await add_customer(db)
    code = await claim_in_review(api, db, redis, bot_under_test, user, venue)
    admin = await make_admin(db, redis, api, AdminRole.REVIEWER)

    response = await api.post(f"/admin/claims/{code}/approve", cookies=admin)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["customer_notified"] is True
    assert (data["claim"]["status"], data["claim"]["notch_no"]) == ("approved", 1)
    assert data["claim"]["reviewed_by"] == "Abhishek"
    claim = await claim_row(db, code)
    visit = await db.get(Visit, claim.visit_id)
    assert visit is not None
    assert (visit.verification_level, visit.code_slot) == ("verified", None)
    assert visit.visited_at == claim.photo_received_at
    scan = await db.get(ScanSession, visit.scan_session_id)
    assert scan is not None and scan.inbound_message_id == claim.photo_message_id
    assert scan.source == ScanSource.BILL_CLAIM and scan.code_age_s is None
    points = await db.scalars(select(PointsEntry.points).where(PointsEntry.visit_id == visit.id))
    total = sum(points.all())
    assert total == 12  # a visit and a first visit here
    assert await redis.zscore(board_key(venue.city_id, ALL_TIME), str(user.id)) == total
    assert meta.texts()[-1] == (
        f"Good news! We checked your bill from {venue.name}: Notch #0001 added, +12 points."
    )
    mine = (await api.get("/me/claims", cookies=await logged_in(api, db, redis, user))).json()[
        "data"
    ]
    assert (mine[0]["status"], mine[0]["notch_no"]) == ("approved", 1)
    # Decided once.
    again = await api.post(f"/admin/claims/{code}/approve", cookies=admin)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "claims.not_in_review"


async def test_approve_checks_the_day_again(
    api: AsyncClient,
    db,
    redis,
    overrides,
    bot_under_test,  # noqa: F811
) -> None:
    venue, _ = await live_venue(db)
    other_cafe, secret = await live_venue(db)
    user = await add_customer(db)
    code = await claim_in_review(api, db, redis, bot_under_test, user, venue)
    # Later that day they scanned at another café.
    await receive(db, bot_under_test, user.phone_e164, scan_text(other_cafe, code_now(secret)))
    admin = await make_admin(db, redis, api)

    response = await api.post(f"/admin/claims/{code}/approve", cookies=admin)

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "They already have a café Notch that day."
    assert (await claim_row(db, code)).status == ClaimStatus.IN_REVIEW


async def test_reject_and_only_free_replies(
    api: AsyncClient,
    db,
    redis,
    overrides,
    meta: FakeMeta,  # noqa: F811
    bot_under_test,  # noqa: F811
) -> None:
    venue, _ = await live_venue(db)
    user = await add_customer(db)
    code = await claim_in_review(api, db, redis, bot_under_test, user, venue)
    # Their last message was over a day ago: a message now would be a paid one.
    await db.execute(
        update(WhatsAppMessage)
        .where(WhatsAppMessage.phone_e164 == user.phone_e164, WhatsAppMessage.direction == "in")
        .values(created_at=datetime.now(UTC) - timedelta(hours=25))
    )
    await db.commit()
    sent_before = len(meta.sent)
    admin = await make_admin(db, redis, api)

    short = await api.post(f"/admin/claims/{code}/reject", json={"reason": "no"}, cookies=admin)
    response = await api.post(
        f"/admin/claims/{code}/reject",
        json={"reason": "The bill is from another day."},
        cookies=admin,
    )

    assert short.status_code == 422
    assert response.status_code == 200, response.text
    assert response.json()["data"]["customer_notified"] is False
    assert len(meta.sent) == sent_before
    mine = (await api.get("/me/claims", cookies=await logged_in(api, db, redis, user))).json()[
        "data"
    ]
    assert (mine[0]["status"], mine[0]["reject_reason"]) == (
        "rejected",
        "The bill is from another day.",
    )
    # A rejected claim can be started again the same day.
    again = await start(api, await logged_in(api, db, redis, user), venue)
    assert again.status_code == 200
    assert again.json()["data"]["claim_code"] != code


async def test_unknown_claim(api: AsyncClient, db, redis) -> None:
    admin = await make_admin(db, redis, api)
    missing = "CLM-7K3M9QX2"

    assert (await api.get(f"/admin/claims/{missing}", cookies=admin)).status_code == 404
    assert (await api.post(f"/admin/claims/{missing}/approve", cookies=admin)).status_code == 404
    assert (await api.get("/admin/claims/not-a-code", cookies=admin)).status_code == 422
