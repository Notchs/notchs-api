"""Scans step 5 on a real database: what the WhatsApp bot does and says after a scan."""

import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fakeredis import FakeAsyncRedis
from httpx import AsyncClient
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clients.whatsapp import SendResult, WhatsAppSendError
from app.core.config import settings
from app.modules.auth.customer_auth import MAX_LINKS_PER_WINDOW, CustomerAuthService
from app.modules.auth.models import AuthToken
from app.modules.scans import replies, service, verifier
from app.modules.scans.models import PointsEntry, RejectReason, ScanSession, Visit
from app.modules.scans.service import MAX_PEOPLE_PER_CODE, MAX_VENUES_PER_DAY
from app.modules.users.consent import CONSENT_VERSION, consent_text
from app.modules.users.models import User, UserStatus
from app.modules.venues.models import Venue, VenueStatus
from app.modules.whatsapp.bot import CONSENT_BUTTON, LOGIN_BUTTON, WhatsAppBot
from app.modules.whatsapp.models import WhatsAppMessage
from app.modules.whatsapp.processor import InboundProcessor
from tests.integration.test_scan_pipeline import add_venue, phone, wrong_code

pytestmark = pytest.mark.asyncio(loop_scope="session")

INTERVAL = 30


class FakeMeta:
    """Stands in for WhatsAppClient: keeps what would have been sent, can fail, can run a check
    at the moment of sending."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.raises: Exception | None = None
        self.on_send = None

    async def send(self, payload: dict[str, Any]) -> SendResult:
        self.sent.append(payload)
        if self.on_send is not None:
            await self.on_send()
        if self.raises is not None:
            raise self.raises
        return SendResult(wa_message_id=f"wamid.OUT{uuid.uuid4().hex}", dry_run=False)

    def texts(self) -> list[str]:
        found = []
        for payload in self.sent:
            match payload["type"]:
                case "interactive":
                    found.append(payload["interactive"]["body"]["text"])
                case "image":
                    found.append(payload["image"]["caption"])
                case _:
                    found.append(payload["text"]["body"])
        return found

    def buttons(self, index: int = -1) -> list[str]:
        action = self.sent[index]["interactive"]["action"]
        return [button["reply"]["id"] for button in action["buttons"]]


@pytest.fixture
def meta() -> FakeMeta:
    return FakeMeta()


@pytest.fixture
def bot_under_test(redis: FakeAsyncRedis, meta: FakeMeta) -> WhatsAppBot:
    return WhatsAppBot(redis, meta)  # type: ignore[arg-type]


def code_now(secret: str) -> str:
    return verifier.code_at(secret, verifier.slot_of(datetime.now(UTC).timestamp(), INTERVAL))


def scan_text(venue: Venue, code: str) -> str:
    return f"Hey Notchs!, get me notched. {venue.scan_code}-{code}"


async def add_customer(db: AsyncSession, *, agreed: bool = True, status: str = "active") -> User:
    now = datetime.now(UTC)
    user = User(
        phone_e164=phone(),
        status=status,
        consent_at=now if agreed else None,
        consent_version=CONSENT_VERSION if agreed else None,
    )
    db.add(user)
    await db.commit()
    return user


async def store(
    db: AsyncSession, sender: str, body: str | None, button_id: str | None = None
) -> WhatsAppMessage:
    payload: dict[str, Any] = {}
    if button_id is not None:
        payload = {
            "message": {
                "type": "interactive",
                "interactive": {
                    "type": "button_reply",
                    "button_reply": {"id": button_id, "title": body},
                },
            }
        }
    message = WhatsAppMessage(
        wa_message_id=f"wamid.{uuid.uuid4().hex}",
        direction="in",
        phone_e164=sender,
        message_type="button" if button_id else "text",
        body=body,
        payload=payload,
        status="received",
    )
    db.add(message)
    await db.flush()
    return message


async def receive(
    db: AsyncSession, bot: WhatsAppBot, sender: str, body: str, button_id: str | None = None
) -> WhatsAppMessage:
    message = await store(db, sender, body, button_id)
    await bot(db, message)
    await db.commit()
    return message


async def visits_of(db: AsyncSession, user: User) -> int:
    return (
        await db.scalar(select(func.count()).select_from(Visit).where(Visit.user_id == user.id))
        or 0
    )


async def scans_of(db: AsyncSession, user: User) -> list[ScanSession]:
    query = select(ScanSession).where(ScanSession.user_id == user.id)
    return list((await db.scalars(query)).all())


def token_of(text: str) -> str:
    link = text.rsplit("\n", 1)[1]
    assert link.startswith(f"{settings.CUSTOMER_APP_URL}/login/verify?token="), link
    return parse_qs(urlsplit(link).query)["token"][0]


# --- a counted scan -------------------------------------------------------------------------------


async def test_a_good_scan_gets_a_stamp_and_a_working_link(
    api: AsyncClient, db, bot_under_test, meta
) -> None:
    venue, secret = await add_venue(db)
    user = await add_customer(db)

    message = await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))

    (text,) = meta.texts()
    assert text.startswith("Stamp added at Kahwa House! Notch #0001. +12 points.")
    assert "rank in your city" in text
    response = await api.post("/auth/customer/consume", json={"token": token_of(text)})
    assert response.status_code == 200, response.text
    assert await visits_of(db, user) == 1
    assert message.processed_at is not None
    assert message.user_id == user.id


async def test_a_scan_makes_a_new_customer_with_their_visit(db, bot_under_test, meta) -> None:
    venue, secret = await add_venue(db)
    sender = phone()

    await receive(db, bot_under_test, sender, scan_text(venue, code_now(secret)))

    user = await db.scalar(select(User).where(User.phone_e164 == sender))
    assert user is not None
    assert await visits_of(db, user) == 1
    assert user.city_id == venue.city_id


async def test_a_message_edited_by_the_customer_still_counts(db, bot_under_test, meta) -> None:
    venue, secret = await add_venue(db)
    user = await add_customer(db)

    text = f"thanks {venue.scan_code.lower()} - {code_now(secret)}"
    await receive(db, bot_under_test, user.phone_e164, text)

    assert await visits_of(db, user) == 1


async def test_the_stamp_is_an_image_when_one_is_set(db, bot_under_test, meta, monkeypatch) -> None:
    image = "https://cdn.notchs.example/stamp.png"
    monkeypatch.setattr(settings, "WHATSAPP_STAMP_IMAGE_URL", image)
    venue, secret = await add_venue(db)
    user = await add_customer(db)

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))

    assert meta.sent[0]["type"] == "image"
    assert meta.sent[0]["image"]["link"] == image
    assert meta.sent[0]["image"]["caption"].startswith("Stamp added at Kahwa House!")
    row = await db.scalar(
        select(WhatsAppMessage).where(
            WhatsAppMessage.direction == "out", WhatsAppMessage.phone_e164 == user.phone_e164
        )
    )
    assert row.message_type == "image"


async def test_the_stamp_goes_out_without_a_link_when_links_run_out(
    db, bot_under_test, meta
) -> None:
    venue, secret = await add_venue(db)
    user = await add_customer(db)
    for _ in range(MAX_LINKS_PER_WINDOW):
        await receive(db, bot_under_test, user.phone_e164, "LOGIN")
    meta.sent.clear()

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))

    (text,) = meta.texts()
    assert text.startswith("Stamp added")
    assert "/login/verify" not in text
    assert "Send LOGIN" in text


async def test_the_stamp_goes_out_without_a_link_when_redis_fails(
    db, bot_under_test, meta, monkeypatch
) -> None:
    async def fails(self, user) -> str:
        raise RedisError("down")

    monkeypatch.setattr(CustomerAuthService, "issue_login_link", fails)
    venue, secret = await add_venue(db)
    user = await add_customer(db)

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))

    assert meta.texts()[0].startswith("Stamp added")
    assert await visits_of(db, user) == 1


# --- consent --------------------------------------------------------------------------------------


async def test_first_scan_counts_and_asks_for_consent_with_no_link(
    db, bot_under_test, meta
) -> None:
    venue, secret = await add_venue(db)
    user = await add_customer(db, agreed=False)

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))

    stamp, question = meta.texts()
    assert stamp.startswith("Stamp added at Kahwa House!")
    assert "/login/verify" not in stamp
    assert "LOGIN" not in stamp
    assert question == consent_text()
    assert meta.buttons() == [CONSENT_BUTTON.id]
    assert await visits_of(db, user) == 1


async def test_agreeing_records_it_and_sends_a_working_link(
    api: AsyncClient, db, bot_under_test, meta
) -> None:
    user = await add_customer(db, agreed=False)

    await receive(
        db, bot_under_test, user.phone_e164, CONSENT_BUTTON.title, button_id=CONSENT_BUTTON.id
    )

    await db.refresh(user)
    assert user.consent_at is not None
    assert user.age_confirmed_at == user.consent_at
    assert user.consent_version == CONSENT_VERSION
    (text,) = meta.texts()
    response = await api.post("/auth/customer/consume", json={"token": token_of(text)})
    assert response.status_code == 200, response.text


async def test_a_second_tap_keeps_the_first_time_and_gets_no_reply(
    db, bot_under_test, meta
) -> None:
    user = await add_customer(db, agreed=False)
    tap = {"body": CONSENT_BUTTON.title, "button_id": CONSENT_BUTTON.id}

    await receive(db, bot_under_test, user.phone_e164, **tap)
    await db.refresh(user)
    first_time = user.consent_at

    await receive(db, bot_under_test, user.phone_e164, **tap)

    await db.refresh(user)
    assert user.consent_at == first_time
    assert len(meta.sent) == 1


async def test_the_exact_consent_wording_is_kept(db, bot_under_test, meta) -> None:
    venue, secret = await add_venue(db)
    user = await add_customer(db, agreed=False)

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))

    bodies = await db.scalars(
        select(WhatsAppMessage.body).where(
            WhatsAppMessage.direction == "out", WhatsAppMessage.phone_e164 == user.phone_e164
        )
    )
    assert consent_text() in bodies.all()


async def test_no_second_consent_question_once_agreed(db, bot_under_test, meta) -> None:
    venue, secret = await add_venue(db)
    user = await add_customer(db, agreed=False)
    await receive(
        db, bot_under_test, user.phone_e164, CONSENT_BUTTON.title, button_id=CONSENT_BUTTON.id
    )
    meta.sent.clear()

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))

    (text,) = meta.texts()
    assert "/login/verify" in text


@pytest.mark.parametrize(
    "tap", [{"body": "LOGIN"}, {"body": "Login link", "button_id": LOGIN_BUTTON.id}]
)
async def test_login_before_agreeing_asks_the_question_and_makes_no_link(
    db, bot_under_test, meta, tap
) -> None:
    user = await add_customer(db, agreed=False)

    await receive(db, bot_under_test, user.phone_e164, **tap)

    assert meta.texts() == [consent_text()]
    assert meta.buttons() == [CONSENT_BUTTON.id]
    links = await db.scalar(
        select(func.count()).select_from(AuthToken).where(AuthToken.user_id == user.id)
    )
    assert links == 0


async def test_login_question_at_most_once_a_minute(db, bot_under_test, meta, redis) -> None:
    user = await add_customer(db, agreed=False)

    await receive(db, bot_under_test, user.phone_e164, "LOGIN")
    await receive(db, bot_under_test, user.phone_e164, "LOGIN")
    assert len(meta.sent) == 1

    await redis.delete(f"wa:notice:consent:{user.id}")  # a minute later
    await receive(db, bot_under_test, user.phone_e164, "LOGIN")
    assert len(meta.sent) == 2


async def test_login_then_agreeing_gives_a_working_link(
    api: AsyncClient, db, bot_under_test, meta
) -> None:
    user = await add_customer(db, agreed=False)
    await receive(db, bot_under_test, user.phone_e164, "LOGIN")

    await receive(
        db, bot_under_test, user.phone_e164, CONSENT_BUTTON.title, button_id=CONSENT_BUTTON.id
    )

    response = await api.post("/auth/customer/consume", json={"token": token_of(meta.texts()[-1])})
    assert response.status_code == 200, response.text


async def test_login_after_agreeing_gives_a_working_link(
    api: AsyncClient, db, bot_under_test, meta
) -> None:
    user = await add_customer(db, agreed=False)
    await receive(
        db, bot_under_test, user.phone_e164, CONSENT_BUTTON.title, button_id=CONSENT_BUTTON.id
    )
    meta.sent.clear()

    await receive(db, bot_under_test, user.phone_e164, "LOGIN")

    (text,) = meta.texts()
    response = await api.post("/auth/customer/consume", json={"token": token_of(text)})
    assert response.status_code == 200, response.text


# --- the sentence with no code --------------------------------------------------------------------


async def test_no_code_gives_a_link_and_no_stamp(
    api: AsyncClient, db, bot_under_test, meta
) -> None:
    user = await add_customer(db)

    await receive(db, bot_under_test, user.phone_e164, "Hey Notchs!, get me notched.")

    (text,) = meta.texts()
    assert replies.SCAN_AT_VENUE in text
    response = await api.post("/auth/customer/consume", json={"token": token_of(text)})
    assert response.status_code == 200, response.text
    assert await visits_of(db, user) == 0
    assert await scans_of(db, user) == []


async def test_no_code_asks_for_consent_first(db, bot_under_test, meta) -> None:
    user = await add_customer(db, agreed=False)

    await receive(db, bot_under_test, user.phone_e164, "Hey Notchs!, get me notched.")

    (text,) = meta.texts()
    assert consent_text() in text and "/login/verify" not in text
    assert meta.buttons() == [CONSENT_BUTTON.id]


async def test_no_code_replies_at_most_once_a_minute(db, bot_under_test, meta, redis) -> None:
    user = await add_customer(db)

    await receive(db, bot_under_test, user.phone_e164, "get me notched")
    await receive(db, bot_under_test, user.phone_e164, "get me notched")
    assert len(meta.sent) == 1

    await redis.delete(f"wa:notice:no_code:{user.id}")  # a minute later
    await receive(db, bot_under_test, user.phone_e164, "get me notched")
    assert len(meta.sent) == 2


# --- refused scans --------------------------------------------------------------------------------


async def test_wrong_code_reply(db, bot_under_test, meta) -> None:
    venue, secret = await add_venue(db)
    user = await add_customer(db)
    wrong = wrong_code(secret, datetime.now(UTC))

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, wrong))

    assert meta.texts() == [replies.rejection_text(RejectReason.WRONG_CODE, None)]
    assert await visits_of(db, user) == 0


async def test_expired_code_reply(db, bot_under_test, meta) -> None:
    venue, secret = await add_venue(db)
    user = await add_customer(db)
    current = verifier.slot_of(datetime.now(UTC).timestamp(), INTERVAL)
    # Two slots past the oldest accepted one, so the clock turning over mid-test cannot matter.
    old = verifier.code_at(secret, current - verifier.SLOTS_BEFORE - 2)

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, old))

    assert meta.texts() == [replies.rejection_text(RejectReason.EXPIRED_CODE, None)]


async def test_already_notched_reply_names_the_venue(db, bot_under_test, meta) -> None:
    venue, secret = await add_venue(db)
    user = await add_customer(db)
    code = code_now(secret)
    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code))

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code))

    assert meta.texts()[-1] == "You're already notched at Kahwa House today. Come back tomorrow!"
    assert await visits_of(db, user) == 1


async def test_daily_limit_reply(db, bot_under_test, meta) -> None:
    user = await add_customer(db)
    for _ in range(MAX_VENUES_PER_DAY):
        venue, secret = await add_venue(db)
        await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))
    venue, secret = await add_venue(db)

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))

    assert meta.texts()[-1] == replies.rejection_text(RejectReason.DAILY_LIMIT, None)
    assert await visits_of(db, user) == MAX_VENUES_PER_DAY


async def test_code_used_reply(db, bot_under_test, meta, monkeypatch) -> None:
    frozen = datetime.now(UTC)

    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    # The real clock could outrun the code's short life over a slow database connection.
    monkeypatch.setattr(service, "datetime", FrozenClock)
    venue, secret = await add_venue(db)
    code = verifier.code_at(secret, verifier.slot_of(frozen.timestamp(), INTERVAL))
    for _ in range(MAX_PEOPLE_PER_CODE):
        user = await add_customer(db)
        await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code))
    late = await add_customer(db)

    await receive(db, bot_under_test, late.phone_e164, scan_text(venue, code))

    assert meta.texts()[-1] == replies.rejection_text(RejectReason.CODE_USED, None)


async def test_unknown_venue_reply_and_log(db, bot_under_test, meta) -> None:
    user = await add_customer(db)

    await receive(db, bot_under_test, user.phone_e164, "NOTCH NOSUCHCODE 482913")

    assert meta.texts() == [replies.rejection_text(RejectReason.UNKNOWN_VENUE, None)]
    (scan,) = await scans_of(db, user)
    assert scan.reject_reason == RejectReason.UNKNOWN_VENUE


async def test_venue_not_live_reply(db, bot_under_test, meta) -> None:
    venue, secret = await add_venue(db)
    venue.status = VenueStatus.PAUSED
    await db.commit()
    user = await add_customer(db)

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))

    assert meta.texts() == [replies.rejection_text(RejectReason.VENUE_UNAVAILABLE, "x")]


async def test_same_refusal_at_most_once_a_minute_but_every_scan_is_logged(
    db, bot_under_test, meta, redis
) -> None:
    venue, secret = await add_venue(db)
    user = await add_customer(db)
    wrong = wrong_code(secret, datetime.now(UTC))

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, wrong))
    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, wrong))

    assert len(meta.sent) == 1
    assert len(await scans_of(db, user)) == 2

    await redis.delete(f"wa:notice:scan_wrong_code:{user.id}")  # a minute later
    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, wrong))
    assert len(meta.sent) == 2


async def test_a_different_refusal_is_still_answered(db, bot_under_test, meta) -> None:
    venue, secret = await add_venue(db)
    user = await add_customer(db)

    await receive(
        db, bot_under_test, user.phone_e164, scan_text(venue, wrong_code(secret, datetime.now(UTC)))
    )
    await receive(db, bot_under_test, user.phone_e164, "NOTCH NOSUCHCODE 482913")

    assert meta.texts() == [
        replies.rejection_text(RejectReason.WRONG_CODE, None),
        replies.rejection_text(RejectReason.UNKNOWN_VENUE, None),
    ]


@pytest.mark.parametrize("status", [UserStatus.BLOCKED, UserStatus.DELETED])
async def test_blocked_customers_get_no_reply_and_no_visit(
    db, bot_under_test, meta, status
) -> None:
    venue, secret = await add_venue(db)
    user = await add_customer(db, status=status)

    await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))

    assert meta.sent == []
    assert await visits_of(db, user) == 0


async def test_only_typed_messages_are_scans(db, bot_under_test, meta) -> None:
    venue, secret = await add_venue(db)
    user = await add_customer(db)

    await receive(
        db,
        bot_under_test,
        user.phone_e164,
        scan_text(venue, code_now(secret)),
        button_id="something-else",
    )

    assert await scans_of(db, user) == []


# --- failures -------------------------------------------------------------------------------------


async def test_a_failed_send_keeps_the_visit_and_records_the_failure(
    db, bot_under_test, meta
) -> None:
    meta.raises = WhatsAppSendError("131047", "outside the window")
    venue, secret = await add_venue(db)
    user = await add_customer(db)

    message = await receive(db, bot_under_test, user.phone_e164, scan_text(venue, code_now(secret)))

    assert await visits_of(db, user) == 1
    assert message.processed_at is not None
    row = await db.scalar(
        select(WhatsAppMessage).where(
            WhatsAppMessage.direction == "out", WhatsAppMessage.phone_e164 == user.phone_e164
        )
    )
    assert (row.status, row.error_code) == ("failed", "131047")


async def test_a_crash_while_replying_never_counts_or_replies_twice(
    db, redis, processor, bot_under_test, meta
) -> None:
    meta.raises = RuntimeError("the server died mid-reply")
    real = InboundProcessor(processor.session_factory, redis, bot_under_test)
    venue, secret = await add_venue(db)
    user = await add_customer(db)
    message = await store(db, user.phone_e164, scan_text(venue, code_now(secret)))
    await db.commit()

    assert await real.process(message.id) is False
    sends = len(meta.sent)

    meta.raises = None
    assert await real.process(message.id) is False  # already done: nothing to retry
    assert await real.sweep() == 0

    assert len(meta.sent) == sends == 1
    assert await visits_of(db, user) == 1
    points = await db.scalar(
        select(func.sum(PointsEntry.points)).where(PointsEntry.user_id == user.id)
    )
    assert points == 12


async def test_scanning_again_after_a_lost_reply_says_already_notched(
    db, redis, processor, bot_under_test, meta
) -> None:
    meta.raises = RuntimeError("the server died mid-reply")
    real = InboundProcessor(processor.session_factory, redis, bot_under_test)
    venue, secret = await add_venue(db)
    user = await add_customer(db)
    code = code_now(secret)
    message = await store(db, user.phone_e164, scan_text(venue, code))
    await db.commit()
    await real.process(message.id)
    meta.raises = None
    meta.sent.clear()

    again = await store(db, user.phone_e164, scan_text(venue, code))
    await db.commit()
    await real.process(again.id)

    assert meta.texts() == ["You're already notched at Kahwa House today. Come back tomorrow!"]


async def test_the_visit_is_saved_and_the_venue_free_while_the_reply_is_sent(
    db_engine, redis, meta, bot_under_test
) -> None:
    """On separate connections, really committed: another scan must not wait for Meta."""
    sessions = async_sessionmaker(db_engine, expire_on_commit=False, autoflush=False)
    async with sessions() as session:
        venue, secret = await add_venue(session)
        user = await add_customer(session)
        message = await store(session, user.phone_e164, scan_text(venue, code_now(secret)))
        message_id = message.id
        await session.commit()
    seen: dict[str, Any] = {}

    async def while_sending() -> None:
        async with sessions() as other:
            seen["visits"] = await visits_of(other, user)
            # NOWAIT: raises at once if the bot still held the venue's lock.
            locked = await other.scalar(
                select(Venue.id).where(Venue.id == venue.id).with_for_update(nowait=True)
            )
            seen["venue_free"] = locked == venue.id
            done = await other.get(WhatsAppMessage, message_id)
            seen["message_done"] = done.processed_at is not None

    meta.on_send = while_sending
    async with sessions() as session:
        row = await session.get(WhatsAppMessage, message_id)
        await bot_under_test(session, row)
        await session.commit()

    assert seen == {"visits": 1, "venue_free": True, "message_done": True}
