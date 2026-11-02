"""Monthly cards: the jobs that make them, and reading and sharing them (C9, C10, A12, E7)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fakeredis import FakeAsyncRedis
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.email import EmailClient, EmailMessage, EmailSendError
from app.modules.cards.builder import average_minute, card_code, vibe
from app.modules.cards.models import CardFace, NotchCard, VenueCard
from app.modules.jobs import runner
from app.modules.jobs.models import JobName, JobRun, RunStatus, RunTrigger
from app.modules.jobs.monthly import JOBS
from app.modules.jobs.runner import SessionFactory
from tests import factories as f

PERIOD = "2026-09"
# 22:40 IST on 1-6 September 2026.
EVENINGS = [datetime(2026, 9, day, 17, 10, tzinfo=UTC) for day in range(1, 7)]


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [([22 * 60 + 40], 22 * 60 + 40), ([23 * 60 + 30, 30], 0), ([600, 720], 660)],
)
def test_average_minute_wraps_around_midnight(minutes: list[int], expected: int) -> None:
    assert average_minute(minutes) == expected


def test_vibe_and_card_code() -> None:
    assert vibe(22 * 60 + 40, "cafe") == "Late-night café hopper"
    assert vibe(9 * 60, "restaurant") == "Early foodie"
    assert card_code(date(2026, 10, 1), 142) == "NC-1026-0142"


async def _run(session_factory: SessionFactory, job: JobName) -> RunStatus:
    run_id = await runner.claim(session_factory, job, PERIOD, trigger=RunTrigger.CLI, rerun=True)
    return await runner.execute(session_factory, run_id, JOBS[job], PERIOD)


async def _month_of_notchs(db: AsyncSession, redis: FakeAsyncRedis):
    city = await f.city(db)
    kahwa = await f.venue(db, city, name="Kahwa House")
    other = await f.venue(db, city, name="Blue Door")
    await f.venue(db, city, name="Never Visited")
    for venue in (kahwa, other):
        await f.counter(db, venue, last_seen=EVENINGS[0])
    riya = await f.user(db, name="Riya Shah")
    for moment in EVENINGS:
        await f.notch_at(db, redis, riya, kahwa, moment)
    await f.notch_at(db, redis, riya, other, EVENINGS[0])
    casual = await f.user(db, name="Kabir Shah")
    await f.notch_at(db, redis, casual, kahwa, EVENINGS[1])
    await f.member(db, kahwa)
    return city, kahwa, riya, casual


async def test_notch_cards_job_makes_one_card_per_customer(
    db: AsyncSession, redis: FakeAsyncRedis, session_factory: SessionFactory
) -> None:
    _, _, riya, casual = await _month_of_notchs(db, redis)

    assert await _run(session_factory, JobName.NOTCH_CARDS) == RunStatus.SUCCEEDED

    card = await db.scalar(select(NotchCard).where(NotchCard.user_id == riya.id))
    assert card.card_code.startswith("NC-0926-")
    assert (card.notch_count, card.places_count) == (7, 2)
    assert (card.top_venue_name, card.top_venue_visits) == ("Kahwa House", 6)
    assert card.regular_since == date(2026, 9, 5)
    assert card.avg_time == "22:40" and card.vibe == "Late-night café hopper"
    assert card.city_rank == 1 and card.rank_percent is None  # under 200 active customers
    assert card.unnotched_count == 1
    assert card.face == CardFace.PLACE and not card.is_simple
    simple = await db.scalar(select(NotchCard).where(NotchCard.user_id == casual.id))
    assert simple.is_simple and simple.city_rank is None
    run = await db.scalar(select(JobRun).where(JobRun.job == JobName.NOTCH_CARDS))
    assert (run.total, run.done, run.failed) == (2, 2, 0)

    # A re-run finds everything done and makes no duplicates.
    assert await _run(session_factory, JobName.NOTCH_CARDS) == RunStatus.SUCCEEDED
    assert len((await db.scalars(select(NotchCard))).all()) == 2


async def test_cards_over_http_and_sharing(
    api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis, session_factory: SessionFactory
) -> None:
    _, _, riya, casual = await _month_of_notchs(db, redis)
    await _run(session_factory, JobName.NOTCH_CARDS)
    api.cookies = f.login(riya)

    (listed,) = (await api.get("/me/cards")).json()["data"]
    latest = (await api.get("/me/cards/latest")).json()["data"]
    assert latest["card_code"] == listed["card_code"]
    assert [face["face"] for face in latest["faces"]] == ["A", "B", "C"]
    assert latest["faces"][1]["proof"] == "Regular since 5 Sep"
    assert latest["faces"][2]["hero"] == "#1"
    assert [screen["kicker"] for screen in latest["reveal"]] == [
        "SEPTEMBER",
        "YOUR PLACE",
        "YOUR VIBE",
        "THE PEAK",
    ]
    code = latest["card_code"]

    changed = await api.patch(f"/me/cards/{code}", json={"face": "A", "revealed": True})
    assert changed.json()["data"]["face"] == "A" and changed.json()["data"]["revealed"]

    api.cookies.clear()
    assert (await api.get(f"/public/cards/{code}")).status_code == 404

    api.cookies = f.login(riya)
    shared = (await api.post(f"/me/cards/{code}/share")).json()["data"]
    assert shared["share_url"].endswith(f"/card/{code}")

    api.cookies.clear()
    public = (await api.get(f"/public/cards/{code}")).json()["data"]
    assert public["first_name"] == "Riya"
    assert public["headline"] == "Riya is #1 in Ahmedabad for September."
    assert [p["name"] for p in public["places"]] == ["Kahwa House", "Blue Door"]

    # Someone who already notched Kahwa House only sees the places they haven't.
    api.cookies = f.login(casual)
    public = (await api.get(f"/public/cards/{code}")).json()["data"]
    assert [p["name"] for p in public["places"]] == ["Blue Door"]

    api.cookies = f.login(riya)
    await api.delete(f"/me/cards/{code}/share")
    api.cookies.clear()
    gone = await api.get(f"/public/cards/{code}")
    assert gone.json()["error"]["code"] == "cards.not_shared"


async def test_simple_card_has_one_face_and_no_reveal(
    api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis, session_factory: SessionFactory
) -> None:
    *_, casual = await _month_of_notchs(db, redis)
    await _run(session_factory, JobName.NOTCH_CARDS)
    api.cookies = f.login(casual)

    card = (await api.get("/me/cards/latest")).json()["data"]
    assert card["reveal"] == []
    (face,) = card["faces"]
    assert (face["unit"], face["proof"]) == ("Notch in September.", "October is yours.")
    rank = await api.patch(f"/me/cards/{card['card_code']}", json={"face": "C"})
    assert rank.json()["error"]["code"] == "cards.face_unavailable"


async def test_venue_cards_job(
    db: AsyncSession, redis: FakeAsyncRedis, session_factory: SessionFactory
) -> None:
    _, kahwa, *_ = await _month_of_notchs(db, redis)

    assert await _run(session_factory, JobName.VENUE_CARDS) == RunStatus.SUCCEEDED

    card = await db.scalar(select(VenueCard).where(VenueCard.venue_id == kahwa.id))
    assert (card.visitors, card.new_visitors, card.notches) == (2, 2, 7)
    assert card.emailed_at is not None  # EMAIL_DRY_RUN logs it


def test_evening_fixture_is_ist_late_night() -> None:
    assert (EVENINGS[0] + timedelta(hours=5, minutes=30)).strftime("%H:%M") == "22:40"


async def test_sharing_twice_keeps_the_first_share_time(
    api: AsyncClient,
    db: AsyncSession,
    redis: FakeAsyncRedis,
    session_factory: SessionFactory,
) -> None:
    _, _, riya, casual = await _month_of_notchs(db, redis)
    await _run(session_factory, JobName.NOTCH_CARDS)
    card = await db.scalar(select(NotchCard).where(NotchCard.user_id == riya.id))
    api.cookies = f.login(riya)

    await api.post(f"/me/cards/{card.card_code}/share")
    await db.refresh(card)
    first = card.shared_at
    await api.post(f"/me/cards/{card.card_code}/share")
    await db.refresh(card)
    assert card.shared_at == first

    # Someone else's card can't be shared or changed.
    api.cookies = f.login(casual)
    assert (await api.post(f"/me/cards/{card.card_code}/share")).status_code == 404


async def test_venue_card_email_failure_is_retried_once(
    db: AsyncSession,
    redis: FakeAsyncRedis,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, kahwa, *_ = await _month_of_notchs(db, redis)
    sent: list[str] = []
    fail = True

    async def send(_: object, message: EmailMessage) -> None:
        if fail:
            raise EmailSendError("Resend returned 500")
        sent.append(message.to)

    monkeypatch.setattr(EmailClient, "send", send)

    assert await _run(session_factory, JobName.VENUE_CARDS) == RunStatus.FAILED
    card = await db.scalar(select(VenueCard).where(VenueCard.venue_id == kahwa.id))
    assert card is not None and card.emailed_at is None  # claim released for the retry
    run = await db.scalar(select(JobRun).where(JobRun.job == JobName.VENUE_CARDS))
    assert run.errors[0]["error"] == "EmailSendError"  # the type only, never the message

    fail = False
    assert await _run(session_factory, JobName.VENUE_CARDS) == RunStatus.SUCCEEDED
    assert await _run(session_factory, JobName.VENUE_CARDS) == RunStatus.SUCCEEDED
    assert len(sent) == 1  # sent exactly once over three runs
