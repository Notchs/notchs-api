"""The customer's profile (`/me`): phone, age and birthday, places by kind, Notchs by location and
the profile picture (team decisions, 18 Nov 2026)."""

import io
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select

from app.common.time import ist_date
from app.modules.consents.models import Consent
from app.modules.locations.models import City, CityStatus
from app.modules.passport.service import MAX_PHOTOS_PER_HOUR
from app.modules.users.consent import BIRTHDAY_CONSENT_TEXT, BIRTHDAY_CONSENT_VERSION
from app.modules.users.deletion import delete_customer
from app.modules.users.photos import MAX_UPLOAD_BYTES, PHOTO_SIZE
from tests.integration.test_leaderboards import ledger_visit, logged_in
from tests.integration.test_passport import add_area, add_venue
from tests.integration.test_scan_bot import add_customer

pytestmark = pytest.mark.asyncio(loop_scope="session")

# India's date, as the app counts ages (UTC is still "yesterday" from 00:00 to 05:30 IST).
TODAY = ist_date(datetime.now(UTC))


def years_ago(years: int, days: int = 0) -> date:
    return TODAY.replace(year=TODAY.year - years) - timedelta(days=days)


def picture(fmt: str = "JPEG", size: tuple[int, int] = (800, 600), *, gps: bool = False) -> bytes:
    image = Image.new("RGB", size, (200, 120, 40))
    out = io.BytesIO()
    if gps:
        exif = Image.Exif()
        exif[0x8825] = {1: "N", 2: (23.0, 2.0, 0.0), 3: "E", 4: (72.0, 34.0, 0.0)}  # GPS
        image.save(out, format=fmt, exif=exif)
    else:
        image.save(out, format=fmt)
    return out.getvalue()


async def upload(api: AsyncClient, cookies, data: bytes, name: str = "me.jpg"):
    return await api.put("/me/photo", files={"photo": (name, data, "image/jpeg")}, cookies=cookies)


# --- GET /me ---------------------------------------------------------------------------------


async def test_profile_has_my_number_and_an_empty_age_section(api: AsyncClient, db, redis) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)

    data = (await api.get("/me", cookies=cookies)).json()["data"]

    assert data["phone"] == user.phone_e164  # their own number, in full
    assert data["photo_url"] is None
    assert data["age"] == {
        "birth_date": None,
        "age_range": None,
        "adult_confirmed_at": None,
        "consent_text": BIRTHDAY_CONSENT_TEXT,
        "consent_version": BIRTHDAY_CONSENT_VERSION,
    }


# --- birthday --------------------------------------------------------------------------------


async def test_saving_a_birthday_records_the_consent_and_shows_the_age_range(
    api: AsyncClient, db, redis
) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)
    born = years_ago(26)

    response = await api.patch("/me", json={"birth_date": born.isoformat()}, cookies=cookies)

    assert response.status_code == 200, response.text
    age = response.json()["data"]["age"]
    assert (age["birth_date"], age["age_range"]) == (born.isoformat(), "25-34")
    (consent,) = (await db.scalars(select(Consent).where(Consent.user_id == user.id))).all()
    assert (consent.purpose, consent.version, consent.channel) == (
        "birthday",
        BIRTHDAY_CONSENT_VERSION,
        "website",
    )
    assert consent.wording == BIRTHDAY_CONSENT_TEXT and consent.withdrawn_at is None

    # Correcting the date keeps the consent already given.
    fixed = await api.patch("/me", json={"birth_date": years_ago(45).isoformat()}, cookies=cookies)
    assert fixed.json()["data"]["age"]["age_range"] == "45+"
    count = len((await db.scalars(select(Consent).where(Consent.user_id == user.id))).all())
    assert count == 1

    # Removing it withdraws the consent.
    removed = await api.patch("/me", json={"birth_date": None}, cookies=cookies)
    assert removed.json()["data"]["age"]["birth_date"] is None
    await db.refresh(consent)
    assert consent.withdrawn_at is not None


@pytest.mark.parametrize(
    "born",
    [years_ago(18, days=-1), date(1899, 12, 31), TODAY + timedelta(days=1)],
    ids=["one-day-short-of-18", "before-1900", "future"],
)
async def test_only_real_dates_of_people_aged_18_or_over(
    api: AsyncClient, db, redis, born: date
) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)

    response = await api.patch("/me", json={"birth_date": born.isoformat()}, cookies=cookies)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "passport.birth_date_invalid"


async def test_turning_18_today_is_fine(api: AsyncClient, db, redis) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)

    response = await api.patch(
        "/me", json={"birth_date": years_ago(18).isoformat()}, cookies=cookies
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["age"]["age_range"] == "18-24"


# --- places by kind and Notchs by location ---------------------------------------------------


async def test_places_by_kind_most_visited_first(api: AsyncClient, db, redis) -> None:
    user = await add_customer(db)
    cafe, _ = await add_venue(db, name="Kahwa House")
    restaurant, _ = await add_venue(db, name="Thali Ghar", category="restaurant")
    for day in (1, 2, 3):
        await ledger_visit(db, user, cafe, 10, at=datetime(2026, 11, day, 9, tzinfo=UTC))
    await ledger_visit(db, user, restaurant, 10, at=datetime(2026, 11, 4, 9, tzinfo=UTC))
    removed = await ledger_visit(db, user, restaurant, 10, at=datetime(2026, 11, 5, 9, tzinfo=UTC))
    removed.status, removed.revoked_reason = "revoked", "Fraud (test)"
    await db.commit()
    cookies = await logged_in(api, db, redis, user)

    response = await api.get("/me/places", cookies=cookies)

    assert response.status_code == 200, response.text
    groups = response.json()["data"]
    assert [(g["niche"], g["places"], g["visits"]) for g in groups] == [
        ("cafe", 1, 3),
        ("restaurant", 1, 1),  # the removed Notch doesn't count
    ]
    (kahwa,) = groups[0]["venues"]
    assert (kahwa["venue_name"], kahwa["visits"]) == ("Kahwa House", 3)
    assert (kahwa["first_visit"], kahwa["last_visit"]) == ("2026-11-01", "2026-11-03")
    assert kahwa["venue_code"] == cafe.venue_code


async def test_notchs_by_city_and_area(api: AsyncClient, db, redis) -> None:
    user = await add_customer(db)
    first, _ = await add_venue(db)
    bodakdev = await add_area(db, first.city_id, "Bodakdev")
    first.area_id = bodakdev.id
    second, _ = await add_venue(
        db, slug=f"second-{uuid.uuid4().hex[:6]}", city_id=first.city_id, area_id=bodakdev.id
    )
    rajkot = City(
        name="Rajkot", slug=f"rjt-{uuid.uuid4().hex[:6]}", state="Gujarat", status=CityStatus.LIVE
    )
    db.add(rajkot)
    await db.commit()
    away, _ = await add_venue(
        db, slug=f"away-{uuid.uuid4().hex[:6]}", city_id=rajkot.id, area_id=None
    )
    await db.commit()
    await ledger_visit(db, user, first, 10, at=datetime(2026, 11, 1, 9, tzinfo=UTC))
    await ledger_visit(db, user, first, 10, at=datetime(2026, 11, 2, 9, tzinfo=UTC))
    await ledger_visit(db, user, second, 10, at=datetime(2026, 11, 3, 9, tzinfo=UTC))
    await ledger_visit(db, user, away, 10, at=datetime(2026, 11, 4, 9, tzinfo=UTC))
    cookies = await logged_in(api, db, redis, user)

    response = await api.get("/me/notches/by-location", cookies=cookies)

    assert response.status_code == 200, response.text
    cities = response.json()["data"]
    assert [(c["city"]["id"], c["notchs"], c["places"]) for c in cities] == [
        (str(first.city_id), 3, 2),
        (str(rajkot.id), 1, 1),
    ]
    assert [(a["area"]["name"], a["notchs"], a["places"]) for a in cities[0]["areas"]] == [
        ("Bodakdev", 3, 2)
    ]
    assert cities[1]["areas"] == [{"area": None, "notchs": 1, "places": 1}]


# --- profile picture -------------------------------------------------------------------------


async def test_upload_cleans_the_picture_and_profile_shows_a_signed_link(
    api: AsyncClient, db, redis, storage
) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)

    response = await upload(api, cookies, picture(gps=True))

    assert response.status_code == 200, response.text
    await db.refresh(user)
    key = user.photo_s3_key
    assert key.startswith(f"users/{user.id}/photo-") and key.endswith(".webp")
    assert response.json()["data"]["photo_url"] == f"https://storage.test/{key}?signature=x"
    with Image.open(io.BytesIO(storage.files[key])) as stored:
        assert (stored.format, stored.size) == ("WEBP", (PHOTO_SIZE, PHOTO_SIZE))
        assert not stored.getexif()  # no GPS or any other metadata kept

    # A new picture replaces the old file.
    again = await upload(api, cookies, picture("PNG", (300, 900)), name="me.png")
    assert again.status_code == 200, again.text
    await db.refresh(user)
    assert list(storage.files) == [user.photo_s3_key] and user.photo_s3_key != key

    removed = await api.delete("/me/photo", cookies=cookies)
    assert removed.status_code == 200
    assert removed.json()["data"]["photo_url"] is None
    assert storage.files == {}


@pytest.mark.parametrize(
    "data",
    [b"not a picture", b"", picture("GIF")],
    ids=["text", "empty", "gif"],
)
async def test_only_real_pictures(api: AsyncClient, db, redis, storage, data: bytes) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)

    response = await upload(api, cookies, data)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "passport.photo_invalid"
    assert storage.files == {}


async def test_pictures_over_5_mb_are_refused(api: AsyncClient, db, redis, storage) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)

    response = await upload(api, cookies, b"\xff" * (MAX_UPLOAD_BYTES + 1))

    assert response.status_code == 422
    assert storage.files == {}


async def test_huge_requests_never_reach_the_api(api: AsyncClient, db, redis) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)

    response = await upload(api, cookies, b"\xff" * 6_500_000)

    assert response.status_code == 413


async def test_new_pictures_per_hour_are_limited(api: AsyncClient, db, redis) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)
    small = picture(size=(64, 64))

    answers = [
        (await upload(api, cookies, small)).status_code for _ in range(MAX_PHOTOS_PER_HOUR + 1)
    ]

    assert answers == [200] * MAX_PHOTOS_PER_HOUR + [429]


async def test_without_storage_there_are_no_pictures(api: AsyncClient, db, redis, storage) -> None:
    storage.configured = False
    user = await add_customer(db)
    user.photo_s3_key = "users/x/photo-old.webp"
    await db.commit()
    cookies = await logged_in(api, db, redis, user)

    response = await upload(api, cookies, picture())

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "passport.photo_storage_not_configured"
    assert (await api.get("/me", cookies=cookies)).json()["data"]["photo_url"] is None


async def test_storage_failure_keeps_the_old_picture(api: AsyncClient, db, redis, storage) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)
    assert (await upload(api, cookies, picture())).status_code == 200
    await db.refresh(user)
    kept = user.photo_s3_key
    storage.fail = True

    response = await upload(api, cookies, picture())

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "passport.photo_storage_failed"
    await db.refresh(user)
    assert user.photo_s3_key == kept


# --- deleting the account --------------------------------------------------------------------


async def test_deleting_the_account_removes_the_picture_and_birthday(
    api: AsyncClient, db, redis, storage
) -> None:
    user = await add_customer(db)
    cookies = await logged_in(api, db, redis, user)
    await api.patch("/me", json={"birth_date": years_ago(30).isoformat()}, cookies=cookies)
    assert (await upload(api, cookies, picture())).status_code == 200
    user_id = user.id

    assert await delete_customer(db, redis, user_id, storage=storage)

    await db.refresh(user)
    assert (user.birth_date, user.photo_s3_key) == (None, None)
    assert storage.files == {}
    assert (await db.scalars(select(Consent).where(Consent.user_id == user_id))).all() == []
