"""The bot end to end: a Meta-shaped payload is stored like the webhook does, handled, and the
replies captured instead of sent (Core Loop B1-B10)."""

from datetime import UTC, datetime

from fakeredis import FakeAsyncRedis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scans.models import Visit
from app.modules.users.deletion import wipe_deleted
from app.modules.users.models import DeletionStatus, User, UserDeletion, UserStatus
from app.modules.venues import scan_codes
from app.modules.whatsapp.bot import WhatsAppBot
from app.modules.whatsapp.dev_router import CapturingClient, meta_payload
from app.modules.whatsapp.models import WhatsAppMessage
from app.modules.whatsapp.service import WhatsAppWebhookService
from app.modules.whatsapp.webhook import parse_webhook
from tests import factories as f

PHONE = "+919812345678"


class Chat:
    """One customer talking to the bot."""

    def __init__(self, db: AsyncSession, redis: FakeAsyncRedis, phone: str = PHONE) -> None:
        self.db, self.redis, self.phone = db, redis, phone

    async def send(self, text: str | None = None, *, button: str | None = None) -> list[str]:
        stored = await WhatsAppWebhookService(self.db).store(
            parse_webhook(meta_payload(self.phone, text, button))
        )
        message = await self.db.get(WhatsAppMessage, stored.new_message_ids[0])
        capture = CapturingClient()
        await WhatsAppBot(self.redis, capture)(self.db, message)  # type: ignore[arg-type]
        await self.db.flush()
        return capture.texts()

    async def user(self) -> User:
        return await self.db.scalar(select(User).where(User.phone_e164 == self.phone))


async def _live_venue(db: AsyncSession):
    city = await f.city(db)
    venue = await f.venue(db, city, name="Kahwa House")
    await f.counter(db, venue)
    return venue


def _scan_text(venue) -> str:
    return scan_codes.scan_text(venue.scan_code, f.code_at(datetime.now(UTC)))


async def test_first_scan_asks_consent_then_notches(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    venue = await _live_venue(db)
    chat = Chat(db, redis)

    replies = await chat.send(_scan_text(venue))
    assert replies == [
        "Welcome to Notchs. Every real visit becomes a Notch. To start, confirm you're 18+ and "
        "agree to our privacy notice.\n[I agree]\n[Privacy notice]"
    ]
    assert "privacy" in (await chat.send(button="privacy"))[0]

    replies = await chat.send(button="consent_yes")
    assert replies[0] == (
        "*Notched.* Kahwa House · Notch #0001\n4 more visits to become the Regular here."
        "\n[My passport]"
    )
    assert replies[1] == "You're Founding Member *#1* of 500.\n[My passport]"
    assert replies[2].startswith("New badge: *First Notch.*")
    user = await chat.user()
    assert user.consent_at is not None and user.consent_version is not None


async def test_second_scan_same_day_and_expired_code(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    venue = await _live_venue(db)
    await f.user(db, phone_e164=PHONE)  # already agreed
    chat = Chat(db, redis)

    assert (await chat.send(_scan_text(venue)))[0].startswith("*Notched.* Kahwa House")
    assert await chat.send(_scan_text(venue)) == [
        "*Already notched* at Kahwa House today. Come back tomorrow.\n[My passport]"
    ]
    stale = scan_codes.scan_text(venue.scan_code, "000000")
    if f.code_at(datetime.now(UTC)) != "000000":
        assert await chat.send(stale) == [
            "This *code expired*. Scan the screen at the counter again."
        ]


async def test_help_login_and_my_data(db: AsyncSession, redis: FakeAsyncRedis) -> None:
    chat = Chat(db, redis)

    (help_reply,) = await chat.send("help")
    assert help_reply.startswith("Send *LOGIN* for your passport link")
    assert help_reply.endswith("[🔑 Login link]")

    (login,) = await chat.send("LOGIN")
    assert "/login/verify?token=" in login and "next=" not in login

    (badges,) = await chat.send(button="badges")
    assert "next=%2Fbadges" in badges

    (data,) = await chat.send("my  data")
    assert "next=%2Fme%2Fprivacy" in data


async def test_anything_else_points_to_help_once(db: AsyncSession, redis: FakeAsyncRedis) -> None:
    chat = Chat(db, redis)

    assert await chat.send("hi, how does this work?") == [
        "I only understand scans. Type *HELP* for options.\n[Help]"
    ]
    assert await chat.send("hello?") == []  # not again within 10 minutes


async def test_delete_then_yes_closes_and_the_wipe_removes_everything(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    venue = await _live_venue(db)
    await f.user(db, phone_e164=PHONE)
    chat = Chat(db, redis)
    await chat.send(_scan_text(venue))

    assert await chat.send("DELETE") == ["*Delete everything?* Reply YES."]
    assert await chat.send("yes") == ["*Done.* Everything is deleted."]
    user = await chat.user()
    assert user.status == UserStatus.DELETED
    assert await chat.send("LOGIN") == []  # closed accounts get no replies

    assert await wipe_deleted(db) == 1
    await db.flush()
    assert await chat.user() is None
    left = await db.scalar(
        select(func.count()).select_from(WhatsAppMessage).where(WhatsAppMessage.phone_e164 == PHONE)
    )
    assert left == 0
    notch = await db.scalar(select(Visit).where(Visit.venue_id == venue.id))
    assert notch is not None and notch.user_id is None  # still counts for the venue
    deletion = await db.scalar(select(UserDeletion))
    assert deletion.status == DeletionStatus.DONE and deletion.phone_last4 == PHONE[-4:]


async def test_yes_without_a_delete_request_does_nothing_harmful(
    db: AsyncSession, redis: FakeAsyncRedis
) -> None:
    await f.user(db, phone_e164=PHONE)
    chat = Chat(db, redis)

    assert await chat.send("YES") == ["I only understand scans. Type *HELP* for options.\n[Help]"]
    assert (await chat.user()).status == UserStatus.ACTIVE
