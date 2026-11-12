"""The WhatsApp bot on a real database: new customers, LOGIN links, help replies."""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fakeredis import FakeAsyncRedis
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.whatsapp import SendResult
from app.core.config import settings
from app.main import app
from app.modules.auth.customer_auth import MAX_LINKS_PER_WINDOW
from app.modules.users.consent import CONSENT_VERSION, consent_text
from app.modules.users.models import User, UserStatus
from app.modules.whatsapp.bot import (
    CONSENT_BUTTON,
    HELP_REPLY,
    LOGIN_BUTTON,
    TOO_MANY_LINKS_REPLY,
    WhatsAppBot,
    find_or_create_customer,
)
from app.modules.whatsapp.models import WhatsAppMessage
from app.modules.whatsapp.processor import InboundProcessor, get_inbound_processor

pytestmark = pytest.mark.asyncio(loop_scope="session")

SECRET = "test-app-secret"


class FakeMeta:
    """Stands in for WhatsAppClient: keeps what would have been sent."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, payload: dict[str, Any]) -> SendResult:
        self.sent.append(payload)
        return SendResult(wa_message_id=f"wamid.OUT{uuid.uuid4().hex}", dry_run=False)

    def texts(self) -> list[str]:
        """Each message's text (for button messages, the text above the buttons)."""
        return [
            p["interactive"]["body"]["text"] if p["type"] == "interactive" else p["text"]["body"]
            for p in self.sent
        ]

    def buttons(self, index: int = -1) -> list[tuple[str, str]]:
        """(id, title) of the buttons in one sent message."""
        action = self.sent[index]["interactive"]["action"]
        return [(b["reply"]["id"], b["reply"]["title"]) for b in action["buttons"]]


@pytest.fixture
def meta() -> FakeMeta:
    return FakeMeta()


@pytest.fixture
def real_bot(redis: FakeAsyncRedis, meta: FakeMeta) -> WhatsAppBot:
    return WhatsAppBot(redis, meta)  # type: ignore[arg-type]


def phone() -> str:
    return f"+9198{uuid.uuid4().int % 10**8:08d}"


async def customer(db: AsyncSession, status: str = UserStatus.ACTIVE) -> User:
    user = User(
        phone_e164=phone(),
        status=status,
        consent_at=datetime.now(UTC),
        consent_version=CONSENT_VERSION,
    )
    db.add(user)
    await db.commit()
    return user


async def receive(
    db: AsyncSession,
    real_bot: WhatsAppBot,
    sender: str,
    body: str | None,
    kind: str = "text",
    button_id: str | None = None,
) -> WhatsAppMessage:
    """Store an incoming message and run the bot on it, as the processor does. With
    `button_id`, a tap on one of our reply buttons (stored as the webhook stores it)."""
    payload: dict[str, Any] = {}
    if button_id is not None:
        kind = "button"
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
        message_type=kind,
        body=body,
        payload=payload,
        status="received",
    )
    db.add(message)
    await db.flush()
    await real_bot(db, message)
    await db.commit()
    return message


async def replies(db: AsyncSession, to: str) -> list[WhatsAppMessage]:
    query = (
        select(WhatsAppMessage)
        .where(WhatsAppMessage.direction == "out", WhatsAppMessage.phone_e164 == to)
        .order_by(WhatsAppMessage.created_at, WhatsAppMessage.id)
    )
    return list((await db.scalars(query)).all())


def token_of(text: str) -> str:
    link = text.rsplit("\n", 1)[1]
    assert link.startswith(f"{settings.CUSTOMER_APP_URL}/login/verify?token="), link
    return parse_qs(urlsplit(link).query)["token"][0]


# --- customers ----------------------------------------------------------------------------------


async def test_first_message_creates_the_customer(
    db: AsyncSession, real_bot: WhatsAppBot, meta: FakeMeta
) -> None:
    sender = phone()

    message = await receive(db, real_bot, sender, "hello")

    user = await db.scalar(select(User).where(User.phone_e164 == sender))
    assert user is not None and user.status == UserStatus.ACTIVE
    assert message.user_id == user.id
    (reply,) = await replies(db, sender)
    assert (reply.body, reply.status, reply.user_id) == (HELP_REPLY, "sent", user.id)
    assert reply.message_type == "button"
    assert reply.wa_message_id.startswith("wamid.OUT")
    assert meta.sent[0]["to"] == sender.removeprefix("+")
    assert meta.buttons() == [(LOGIN_BUTTON.id, LOGIN_BUTTON.title)]


async def test_same_number_is_one_customer(db: AsyncSession) -> None:
    sender = phone()

    first = await find_or_create_customer(db, sender)
    again = await find_or_create_customer(db, sender)

    assert first.id == again.id
    count = await db.scalar(select(func.count()).select_from(User).where(User.phone_e164 == sender))
    assert count == 1


@pytest.mark.parametrize("status", [UserStatus.BLOCKED, UserStatus.DELETED])
async def test_blocked_or_deleted_customers_get_no_reply(
    db: AsyncSession, real_bot: WhatsAppBot, meta: FakeMeta, status: str
) -> None:
    user = await customer(db, status)

    message = await receive(db, real_bot, user.phone_e164, "LOGIN")

    assert message.user_id == user.id
    assert meta.sent == []
    assert await replies(db, user.phone_e164) == []


# --- LOGIN --------------------------------------------------------------------------------------


async def test_tapping_the_login_button_sends_a_working_link(
    api: AsyncClient, db: AsyncSession, real_bot: WhatsAppBot, meta: FakeMeta
) -> None:
    user = await customer(db)
    await receive(db, real_bot, user.phone_e164, "hi")

    await receive(db, real_bot, user.phone_e164, LOGIN_BUTTON.title, button_id=LOGIN_BUTTON.id)

    help_text, login_text = meta.texts()
    assert help_text == HELP_REPLY
    response = await api.post("/auth/customer/consume", json={"token": token_of(login_text)})
    assert response.status_code == 200, response.text


async def test_tapping_an_unknown_button_gets_help(
    db: AsyncSession, real_bot: WhatsAppBot, meta: FakeMeta
) -> None:
    user = await customer(db)

    await receive(db, real_bot, user.phone_e164, "Matcha", button_id="tag:matcha")

    assert meta.texts() == [HELP_REPLY]


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ("LOGIN", "text"),
        ("  login ", "text"),
        ("Login", "button"),
        ("Hey Notchs, I want to login", "text"),
    ],
)
async def test_login_replies_with_a_working_link(
    api: AsyncClient, db: AsyncSession, real_bot: WhatsAppBot, meta: FakeMeta, body: str, kind: str
) -> None:
    user = await customer(db)

    await receive(db, real_bot, user.phone_e164, body, kind)

    (text,) = meta.texts()
    assert "works once" in text
    response = await api.post("/auth/customer/consume", json={"token": token_of(text)})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["id"] == str(user.id)


async def test_too_many_links(db: AsyncSession, real_bot: WhatsAppBot, meta: FakeMeta) -> None:
    user = await customer(db)

    for _ in range(MAX_LINKS_PER_WINDOW + 2):
        await receive(db, real_bot, user.phone_e164, "LOGIN")

    texts = meta.texts()
    assert len(texts) == MAX_LINKS_PER_WINDOW + 1  # the last ask gets nothing
    assert all("/login/verify?token=" in t for t in texts[:MAX_LINKS_PER_WINDOW])
    assert texts[-1] == TOO_MANY_LINKS_REPLY


async def test_too_many_links_is_answered_even_right_after_help(
    db: AsyncSession, real_bot: WhatsAppBot, meta: FakeMeta
) -> None:
    """Seen on staging: help, then a 4th LOGIN within 15 minutes got no answer at all."""
    user = await customer(db)
    for _ in range(MAX_LINKS_PER_WINDOW):
        await receive(db, real_bot, user.phone_e164, "LOGIN")
    await receive(db, real_bot, user.phone_e164, "hi")

    await receive(db, real_bot, user.phone_e164, LOGIN_BUTTON.title, button_id=LOGIN_BUTTON.id)

    assert meta.texts()[-2:] == [HELP_REPLY, TOO_MANY_LINKS_REPLY]


# --- help ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "kind"),
    # A photo gets the claims answer instead (test_claims.py).
    [("hi", "text"), ("what is notchs?", "text"), (None, "other")],
)
async def test_anything_else_gets_help(
    db: AsyncSession, real_bot: WhatsAppBot, meta: FakeMeta, body: str | None, kind: str
) -> None:
    user = await customer(db)

    await receive(db, real_bot, user.phone_e164, body, kind)

    assert meta.texts() == [HELP_REPLY]


async def test_help_at_most_once_every_ten_minutes(
    db: AsyncSession, real_bot: WhatsAppBot, meta: FakeMeta, redis: FakeAsyncRedis
) -> None:
    user = await customer(db)

    await receive(db, real_bot, user.phone_e164, "hi")
    await receive(db, real_bot, user.phone_e164, "hello?")
    assert meta.texts() == [HELP_REPLY]

    await redis.delete(f"wa:notice:help:{user.id}")  # ten minutes later
    await receive(db, real_bot, user.phone_e164, "anyone?")
    assert meta.texts() == [HELP_REPLY, HELP_REPLY]


async def test_login_still_works_right_after_help(
    db: AsyncSession, real_bot: WhatsAppBot, meta: FakeMeta
) -> None:
    user = await customer(db)

    await receive(db, real_bot, user.phone_e164, "hi")
    await receive(db, real_bot, user.phone_e164, "LOGIN")

    help_text, login_text = meta.texts()
    assert help_text == HELP_REPLY
    assert "/login/verify?token=" in login_text


# --- through the webhook ------------------------------------------------------------------------


async def test_login_from_a_new_number_asks_for_consent_through_the_webhook(
    api: AsyncClient,
    db: AsyncSession,
    processor: InboundProcessor,
    real_bot: WhatsAppBot,
    meta: FakeMeta,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", SecretStr(SECRET))
    real = InboundProcessor(processor.session_factory, processor.redis, real_bot)
    app.dependency_overrides[get_inbound_processor] = lambda: real  # the api fixture undoes it
    sender = phone()
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "from": sender.removeprefix("+"),
                                    "id": f"wamid.{uuid.uuid4().hex}",
                                    "type": "text",
                                    "text": {"body": "LOGIN"},
                                }
                            ]
                        },
                    }
                ]
            }
        ],
    }
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()

    response = await api.post(
        "/webhooks/whatsapp",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature},
    )

    assert response.status_code == 200
    user = await db.scalar(select(User).where(User.phone_e164 == sender))
    assert user is not None
    inbound = await db.scalar(
        select(WhatsAppMessage)
        .where(WhatsAppMessage.direction == "in", WhatsAppMessage.phone_e164 == sender)
        .execution_options(populate_existing=True)
    )
    assert inbound is not None
    assert inbound.processed_at is not None and inbound.user_id == user.id
    (reply,) = await replies(db, sender)
    assert reply.body == consent_text()
    assert meta.buttons() == [(CONSENT_BUTTON.id, CONSENT_BUTTON.title)]


async def test_a_handle_taken_at_the_same_instant_gets_another(
    db: AsyncSession, monkeypatch
) -> None:
    """Two people with the same name join together: the second insert meets the first's handle."""
    from app.modules.whatsapp import bot as bot_module

    existing = await bot_module.find_or_create_customer(db, phone(), "Riya M")
    await db.commit()
    handles = iter([existing.handle, existing.handle, "riya.m999999"])

    async def racing_handle(name, *, taken):
        return next(handles)

    monkeypatch.setattr(bot_module, "free_handle", racing_handle)

    newcomer = await bot_module.find_or_create_customer(db, phone(), "Riya M")

    assert newcomer.id != existing.id
    assert newcomer.handle == "riya.m999999"
