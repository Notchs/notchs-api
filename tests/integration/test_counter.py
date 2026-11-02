"""Step 7 on a real database: pairing the venue's counter phone, its display and heartbeat."""

import base64
import hashlib
import hmac
import re
import struct
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import crypto
from app.core.config import settings
from app.modules.auth.models import AuthToken
from app.modules.auth.tokens import hash_token
from app.modules.auth.venue_auth import VenueAuthService
from app.modules.locations.models import City
from app.modules.venues.counters import MAX_PAIRING_LINKS_PER_HOUR
from app.modules.venues.models import CounterDevice, Venue, VenueMember, VenueStatus

pytestmark = pytest.mark.asyncio(loop_scope="session")


def cookie_header(response: Response, name: str) -> str:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header
    raise AssertionError(f"{name} not set")


def cookie(response: Response, name: str) -> str:
    return cookie_header(response, name).split(";", 1)[0].split("=", 1)[1]


def totp(secret: str, at: float, interval: int = 30, digits: int = 6) -> str:
    """RFC 6238, written out here so the test does not trust the code under test."""
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", int(at // interval)), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 10**digits:0{digits}d}"


@pytest.fixture
async def owner(db: AsyncSession, redis, api: AsyncClient) -> dict[str, object]:
    """A signed venue whose owner is logged in. Returns ids and the owner's access cookie."""
    city = City(name="Ahmedabad", slug=f"ahd-{uuid.uuid4().hex[:6]}", state="Gujarat")
    db.add(city)
    await db.flush()
    venue = Venue(
        city_id=city.id,
        name="Kahwa House",
        slug="kahwa-house",
        category="cafe",
        status=VenueStatus.SIGNED,
    )
    db.add(venue)
    await db.flush()
    member = VenueMember(
        venue_id=venue.id,
        phone_e164="+919800000001",
        name="Riya",
        email=f"o-{uuid.uuid4().hex[:6]}@kahwa.in",
    )
    db.add(member)
    await db.flush()
    welcome = await VenueAuthService(db, redis).welcome_email(member, venue)
    await db.commit()
    token = parse_qs(urlsplit(re.search(r"https?://\S+", welcome.text)[0]).query)["token"][0]
    logged_in = await api.post(
        "/venue/auth/set-password", json={"token": token, "password": "owner pass 1984"}
    )
    api.cookies.clear()
    return {
        "venue_id": venue.id,
        "venue_code": venue.venue_code,
        "cookies": {"nx_venue_at": cookie(logged_in, "nx_venue_at")},
    }


async def pairing_token(api: AsyncClient, owner) -> str:
    response = await api.post("/venue/counter/pairing-link", cookies=owner["cookies"])
    assert response.status_code == 201, response.text
    url = response.json()["data"]["url"]
    assert url.startswith(f"{settings.VENUE_APP_URL}/display/pair?token=")
    return parse_qs(urlsplit(url).query)["token"][0]


async def pair(api: AsyncClient, token: str) -> Response:
    response = await api.post("/display/pair", json={"token": token})
    api.cookies.clear()
    return response


async def display_config(api: AsyncClient, device_cookie: str) -> Response:
    response = await api.get("/display/config", cookies={"nx_device": device_cookie})
    api.cookies.clear()
    return response


async def test_no_counter_yet(api: AsyncClient, owner) -> None:
    response = await api.get("/venue/counter", cookies=owner["cookies"])

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "counters.not_found"


async def test_pair_end_to_end(api: AsyncClient, db: AsyncSession, owner) -> None:
    token = await pairing_token(api, owner)
    before = (await api.get("/venue/counter", cookies=owner["cookies"])).json()["data"]

    paired = await pair(api, token)

    assert before["paired"] is False and before["label"] == "Counter 1"
    assert paired.status_code == 200, paired.text
    header = cookie_header(paired, "nx_device").lower()
    assert f"path={settings.API_V1_PREFIX}/display" in header and "httponly" in header
    assert "max-age=31536000" in header
    assert paired.headers["cache-control"] == "no-store"
    data = paired.json()["data"]
    assert data["venue"]["venue_code"] == owner["venue_code"]
    assert data["totp"]["interval_s"] == 30 and data["totp"]["digits"] == 6
    secret = data["totp"]["secret"]
    assert re.fullmatch(r"[A-Z2-7]{32}", secret)
    assert re.fullmatch(r"\d{6}", totp(secret, time.time()))  # a usable RFC 6238 secret

    status = (await api.get("/venue/counter", cookies=owner["cookies"])).json()["data"]
    assert status["paired"] is True and status["online"] is True
    venue = await db.get(Venue, owner["venue_id"])
    await db.refresh(venue)
    assert venue.status == VenueStatus.COUNTER_LIVE  # the first pairing puts the venue live


async def test_secret_is_stored_encrypted(api: AsyncClient, db: AsyncSession, owner) -> None:
    secret = (await pair(api, await pairing_token(api, owner))).json()["data"]["totp"]["secret"]

    stored = await db.scalar(
        select(CounterDevice).where(CounterDevice.venue_id == owner["venue_id"])
    )

    assert secret not in stored.totp_secret_enc
    assert crypto.decrypt(stored.totp_secret_enc) == secret


async def test_display_config_needs_the_device_cookie(api: AsyncClient, owner) -> None:
    device = cookie(await pair(api, await pairing_token(api, owner)), "nx_device")

    ok = await display_config(api, device)
    missing = await api.get("/display/config")
    made_up = await display_config(api, "x" * 43)

    assert ok.status_code == 200 and ok.headers["cache-control"] == "no-store"
    assert missing.status_code == made_up.status_code == 401
    assert missing.json()["error"]["code"] == "devices.not_paired"


async def test_device_cookie_is_not_a_dashboard_login(api: AsyncClient, owner) -> None:
    device = cookie(await pair(api, await pairing_token(api, owner)), "nx_device")

    response = await api.get("/venue/me", cookies={"nx_venue_at": device})

    assert response.status_code == 401


async def test_pairing_link_works_once(api: AsyncClient, owner) -> None:
    token = await pairing_token(api, owner)

    assert (await pair(api, token)).status_code == 200
    again = await pair(api, token)

    assert again.status_code == 400
    assert again.json()["error"]["code"] == "auth.token_invalid"


async def test_pairing_link_expires(api: AsyncClient, db: AsyncSession, owner) -> None:
    token = await pairing_token(api, owner)
    await db.execute(
        update(AuthToken)
        .where(AuthToken.token_hash == hash_token(token))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.commit()

    assert (await pair(api, token)).status_code == 400


async def test_repairing_replaces_the_phone_and_the_secret(
    api: AsyncClient, db: AsyncSession, owner
) -> None:
    old = await pair(api, await pairing_token(api, owner))
    new = await pair(api, await pairing_token(api, owner))

    assert old.json()["data"]["counter_id"] == new.json()["data"]["counter_id"]  # same counter
    assert old.json()["data"]["totp"]["secret"] != new.json()["data"]["totp"]["secret"]
    assert (await display_config(api, cookie(old, "nx_device"))).status_code == 401
    assert (await display_config(api, cookie(new, "nx_device"))).status_code == 200
    rows = await db.scalars(
        select(CounterDevice).where(CounterDevice.venue_id == owner["venue_id"])
    )
    assert len(rows.all()) == 1


async def test_old_phone_works_until_the_new_one_pairs(api: AsyncClient, owner) -> None:
    old = cookie(await pair(api, await pairing_token(api, owner)), "nx_device")

    await pairing_token(api, owner)  # a new link, not used yet

    assert (await display_config(api, old)).status_code == 200


async def test_unpair(api: AsyncClient, db: AsyncSession, owner) -> None:
    first = await pair(api, await pairing_token(api, owner))
    device = cookie(first, "nx_device")

    removed = await api.delete("/venue/counter", cookies=owner["cookies"])
    api.cookies.clear()

    assert removed.status_code == 204
    assert (await display_config(api, device)).status_code == 401
    assert (await api.get("/venue/counter", cookies=owner["cookies"])).status_code == 404
    second = await pair(api, await pairing_token(api, owner))
    assert second.json()["data"]["counter_id"] != first.json()["data"]["counter_id"]
    history = await db.scalars(
        select(CounterDevice).where(CounterDevice.venue_id == owner["venue_id"])
    )
    assert len(history.all()) == 2  # the removed one is kept, revoked


async def test_rename(api: AsyncClient, owner) -> None:
    device = cookie(await pair(api, await pairing_token(api, owner)), "nx_device")

    renamed = await api.patch(
        "/venue/counter", json={"label": "Front desk"}, cookies=owner["cookies"]
    )
    api.cookies.clear()

    assert renamed.json()["data"]["label"] == "Front desk"
    assert (await display_config(api, device)).json()["data"]["label"] == "Front desk"
    too_long = await api.patch("/venue/counter", json={"label": "x" * 41}, cookies=owner["cookies"])
    assert too_long.status_code == 422


async def test_heartbeat_updates_last_seen(api: AsyncClient, db: AsyncSession, owner) -> None:
    device = cookie(await pair(api, await pairing_token(api, owner)), "nx_device")
    stale = datetime.now(UTC) - timedelta(minutes=5)
    await db.execute(
        update(CounterDevice)
        .where(CounterDevice.venue_id == owner["venue_id"])
        .values(last_seen_at=stale)
    )
    await db.commit()
    offline = (await api.get("/venue/counter", cookies=owner["cookies"])).json()["data"]

    beat = await api.post("/display/heartbeat", cookies={"nx_device": device})
    api.cookies.clear()

    assert offline["online"] is False
    assert beat.status_code == 204
    assert "max-age=31536000" in cookie_header(beat, "nx_device").lower()  # renewed
    online = (await api.get("/venue/counter", cookies=owner["cookies"])).json()["data"]
    assert online["online"] is True


async def test_a_paused_venue_stays_paused(api: AsyncClient, db: AsyncSession, owner) -> None:
    await db.execute(
        update(Venue).where(Venue.id == owner["venue_id"]).values(status=VenueStatus.PAUSED)
    )
    await db.commit()

    await pair(api, await pairing_token(api, owner))

    venue = await db.get(Venue, owner["venue_id"])
    await db.refresh(venue)
    assert venue.status == VenueStatus.PAUSED


async def test_counter_endpoints_need_the_owner_login(api: AsyncClient) -> None:
    assert (await api.get("/venue/counter")).status_code == 401
    assert (await api.post("/venue/counter/pairing-link")).status_code == 401


async def test_pairing_link_rate_limit(api: AsyncClient, owner) -> None:
    for _ in range(MAX_PAIRING_LINKS_PER_HOUR):
        await pairing_token(api, owner)

    response = await api.post("/venue/counter/pairing-link", cookies=owner["cookies"])

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "counters.rate_limited"


async def test_database_allows_one_active_counter_per_venue(db: AsyncSession, owner) -> None:
    db.add(CounterDevice(venue_id=owner["venue_id"], totp_secret_enc=crypto.encrypt("A" * 32)))
    await db.flush()
    db.add(CounterDevice(venue_id=owner["venue_id"], totp_secret_enc=crypto.encrypt("B" * 32)))

    with pytest.raises(IntegrityError, match="uq_counter_devices_active_venue"):
        await db.flush()
    await db.rollback()
