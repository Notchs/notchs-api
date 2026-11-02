"""The WhatsApp webhook on a real database: signatures, storing, duplicates, statuses."""

import hashlib
import hmac
import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.users.models import User
from app.modules.whatsapp.models import WhatsAppMessage

pytestmark = pytest.mark.asyncio(loop_scope="session")

SECRET = "test-app-secret"
VERIFY = "test-verify-token"


@pytest.fixture(autouse=True)
def meta_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", SecretStr(SECRET))
    monkeypatch.setattr(settings, "WHATSAPP_VERIFY_TOKEN", SecretStr(VERIFY))


def phone() -> str:
    return f"9198{uuid.uuid4().int % 10**8:08d}"


def envelope(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": value}]}],
    }


def text_from(sender: str, body: str, wa_id: str | None = None) -> dict[str, Any]:
    return envelope(
        {
            "contacts": [{"wa_id": sender, "profile": {"name": "Riya"}}],
            "messages": [
                {
                    "from": sender,
                    "id": wa_id or f"wamid.{uuid.uuid4().hex}",
                    "type": "text",
                    "text": {"body": body},
                }
            ],
        }
    )


async def post(
    api: AsyncClient, payload: Any, *, secret: str = SECRET, raw: bytes | None = None
) -> Response:
    body = raw if raw is not None else json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return await api.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature},
    )


async def rows(db: AsyncSession, **where: Any) -> list[WhatsAppMessage]:
    query = select(WhatsAppMessage).filter_by(**where)
    return list((await db.scalars(query)).all())


# --- Meta's verification ----------------------------------------------------------------------


async def test_verification_handshake(api: AsyncClient) -> None:
    response = await api.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1158201444"},
    )

    assert response.status_code == 200
    assert response.text == "1158201444"


@pytest.mark.parametrize(
    "params",
    [
        {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
        {"hub.mode": "unsubscribe", "hub.verify_token": VERIFY, "hub.challenge": "1"},
        {},
    ],
    ids=["wrong-token", "wrong-mode", "nothing"],
)
async def test_verification_refused(api: AsyncClient, params: dict[str, str]) -> None:
    assert (await api.get("/webhooks/whatsapp", params=params)).status_code == 403


# --- signatures ---------------------------------------------------------------------------------


async def test_unsigned_or_wrongly_signed_calls_are_refused(
    api: AsyncClient, db: AsyncSession
) -> None:
    sender = phone()
    payload = text_from(sender, "hi")

    unsigned = await api.post("/webhooks/whatsapp", json=payload)
    wrong = await post(api, payload, secret="not-the-secret")

    assert unsigned.status_code == wrong.status_code == 401
    assert await rows(db, phone_e164=f"+{sender}") == []


async def test_no_app_secret_configured(api: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", None)

    assert (await post(api, text_from(phone(), "hi"))).status_code == 503


# --- inbound messages ---------------------------------------------------------------------------


async def test_text_message_is_stored(api: AsyncClient, db: AsyncSession) -> None:
    sender = phone()

    response = await post(api, text_from(sender, "NOTCH KAHWA 482913"))

    assert response.status_code == 200
    (row,) = await rows(db, phone_e164=f"+{sender}")
    assert (row.direction, row.status, row.message_type) == ("in", "received", "text")
    assert row.body == "NOTCH KAHWA 482913"
    assert row.payload["message"]["text"]["body"] == "NOTCH KAHWA 482913"
    assert row.user_id is None  # no customer yet: created by the bot later


async def test_message_from_a_known_customer_is_linked(api: AsyncClient, db: AsyncSession) -> None:
    sender = phone()
    user = User(phone_e164=f"+{sender}")
    db.add(user)
    await db.commit()

    await post(api, text_from(sender, "LOGIN"))

    (row,) = await rows(db, phone_e164=f"+{sender}")
    assert row.user_id == user.id


async def test_meta_redelivering_a_message_stores_it_once(
    api: AsyncClient, db: AsyncSession
) -> None:
    sender, wa_id = phone(), f"wamid.{uuid.uuid4().hex}"

    first = await post(api, text_from(sender, "hi", wa_id))
    again = await post(api, text_from(sender, "hi", wa_id))

    assert first.status_code == again.status_code == 200
    assert len(await rows(db, wa_message_id=wa_id)) == 1


async def test_other_kinds_and_button_taps(api: AsyncClient, db: AsyncSession) -> None:
    sender = phone()
    payload = envelope(
        {
            "messages": [
                {
                    "from": sender,
                    "id": f"wamid.{uuid.uuid4().hex}",
                    "type": "audio",
                    "audio": {"id": "M1"},
                },
                {
                    "from": sender,
                    "id": f"wamid.{uuid.uuid4().hex}",
                    "type": "interactive",
                    "interactive": {
                        "type": "button_reply",
                        "button_reply": {"id": "name:yes", "title": "Use Riya"},
                    },
                },
            ]
        }
    )

    await post(api, payload)

    kinds = sorted((r.message_type, r.body) for r in await rows(db, phone_e164=f"+{sender}"))
    assert kinds == [("button", "Use Riya"), ("other", None)]


async def test_signed_but_unreadable_body_gets_200(api: AsyncClient) -> None:
    assert (await post(api, None, raw=b"not json")).status_code == 200


async def test_oversized_body(api: AsyncClient) -> None:
    assert (await post(api, None, raw=b"x" * 1_000_001)).status_code == 413


# --- statuses of our sent messages ----------------------------------------------------------------


async def sent_message(db: AsyncSession, status: str = "sent") -> str:
    wa_id = f"wamid.OUT{uuid.uuid4().hex}"
    db.add(
        WhatsAppMessage(
            wa_message_id=wa_id,
            direction="out",
            phone_e164="+919876543210",
            message_type="text",
            body="Stamp added!",
            status=status,
        )
    )
    await db.commit()
    return wa_id


def status(wa_id: str, value: str, **extra: Any) -> dict[str, Any]:
    return envelope(
        {"statuses": [{"id": wa_id, "status": value, "recipient_id": "919876543210", **extra}]}
    )


async def current(db: AsyncSession, wa_id: str) -> WhatsAppMessage:
    (row,) = await rows(db, wa_message_id=wa_id)
    await db.refresh(row)
    return row


async def test_statuses_move_forward(api: AsyncClient, db: AsyncSession) -> None:
    wa_id = await sent_message(db)

    await post(api, status(wa_id, "delivered"))
    assert (await current(db, wa_id)).status == "delivered"
    await post(api, status(wa_id, "read"))
    await post(api, status(wa_id, "delivered"))  # arrives late: ignored

    assert (await current(db, wa_id)).status == "read"


async def test_failed_status_keeps_metas_error_code(api: AsyncClient, db: AsyncSession) -> None:
    wa_id = await sent_message(db)

    await post(
        api, status(wa_id, "failed", errors=[{"code": 131047, "title": "Re-engagement message"}])
    )

    row = await current(db, wa_id)
    assert (row.status, row.error_code) == ("failed", "131047")


async def test_status_for_an_unknown_or_inbound_message_changes_nothing(
    api: AsyncClient, db: AsyncSession
) -> None:
    sender, wa_id = phone(), f"wamid.{uuid.uuid4().hex}"
    await post(api, text_from(sender, "hi", wa_id))

    unknown = await post(api, status("wamid.NOT_OURS", "read"))
    inbound = await post(api, status(wa_id, "read"))

    assert unknown.status_code == inbound.status_code == 200
    assert (await current(db, wa_id)).status == "received"
    count = await db.scalar(
        select(func.count())
        .select_from(WhatsAppMessage)
        .where(WhatsAppMessage.wa_message_id == "wamid.NOT_OURS")
    )
    assert count == 0
