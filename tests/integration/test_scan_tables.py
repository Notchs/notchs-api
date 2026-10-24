"""Scans step 3 on a real database: customer names and handles from WhatsApp, and the rules the
scan sessions, visits and points tables enforce themselves."""

import re
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.locations.models import City
from app.modules.scans.models import (
    PointsEntry,
    PointsReason,
    RejectReason,
    ScanSession,
    ScanStatus,
    Visit,
)
from app.modules.users.handles import HANDLE_REGEX
from app.modules.users.models import User
from app.modules.venues.models import Venue
from app.modules.whatsapp.bot import find_or_create_customer
from app.modules.whatsapp.models import WhatsAppMessage
from app.modules.whatsapp.service import WhatsAppWebhookService
from app.modules.whatsapp.webhook import parse_webhook

pytestmark = pytest.mark.asyncio(loop_scope="session")


def phone() -> str:
    return f"+9198{uuid.uuid4().int % 10**8:08d}"


@pytest.fixture
async def venue(db: AsyncSession) -> Venue:
    city = City(name="Ahmedabad", slug=f"ahd-{uuid.uuid4().hex[:6]}", state="Gujarat")
    db.add(city)
    await db.flush()
    row = Venue(city_id=city.id, name="Kahwa House", slug="kahwa-house", category="cafe")
    db.add(row)
    await db.commit()
    return row


@pytest.fixture
async def customer(db: AsyncSession) -> User:
    return await find_or_create_customer(db, phone(), "Riya Mehta")


async def rejected(db: AsyncSession, row: object) -> bool:
    """True if the database refuses the row."""
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        return True
    return False


# --- names and handles ---------------------------------------------------------------------------


async def test_new_customer_gets_the_whatsapp_name_and_a_handle(db: AsyncSession) -> None:
    user = await find_or_create_customer(db, phone(), "  Riya   Mehta ")

    assert user.name == "Riya Mehta"
    assert re.fullmatch(r"riya\.m[1-9][0-9]{3}", user.handle)


async def test_same_name_different_handles(db: AsyncSession) -> None:
    first = await find_or_create_customer(db, phone(), "Riya Mehta")
    second = await find_or_create_customer(db, phone(), "Riya Mehta")

    assert first.name == second.name
    assert first.handle != second.handle


async def test_no_name_yet_then_one_arrives(db: AsyncSession) -> None:
    number = phone()
    nameless = await find_or_create_customer(db, number, None)
    assert nameless.name is None and nameless.handle.startswith("notcher")

    later = await find_or_create_customer(db, number, "Riya Mehta")

    assert later.id == nameless.id and later.name == "Riya Mehta"
    assert later.handle == nameless.handle  # the handle stays: links keep working


async def test_a_name_already_set_is_kept(db: AsyncSession) -> None:
    number = phone()
    user = await find_or_create_customer(db, number, "Riya Mehta")
    user.name = "Riya M (changed on the website)"
    await db.flush()

    again = await find_or_create_customer(db, number, "Riya WhatsApp Name")

    assert again.name == "Riya M (changed on the website)"


async def test_the_webhook_keeps_the_profile_name_for_the_bot(db: AsyncSession) -> None:
    sender = phone()
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "contacts": [{"wa_id": sender[1:], "profile": {"name": "Riya Mehta"}}],
                            "messages": [
                                {
                                    "from": sender[1:],
                                    "id": f"wamid.{uuid.uuid4().hex}",
                                    "type": "text",
                                    "text": {"body": "hi"},
                                }
                            ],
                        },
                    }
                ]
            }
        ],
    }

    stored = await WhatsAppWebhookService(db).store(parse_webhook(payload))

    message = await db.get(WhatsAppMessage, stored.new_message_ids[0])
    assert message.payload["profile_name"] == "Riya Mehta"


async def test_the_database_checks_handles(db: AsyncSession) -> None:
    taken = (await find_or_create_customer(db, phone(), "Riya Mehta")).handle

    assert await rejected(db, User(phone_e164=phone(), handle=taken))
    assert await rejected(db, User(phone_e164=phone(), handle="Has Spaces"))
    assert await rejected(db, User(phone_e164=phone(), handle="UPPER"))
    made = User(phone_e164=phone())  # outside the bot: a random handle
    assert not await rejected(db, made)
    assert re.fullmatch(HANDLE_REGEX, made.handle)


# --- scan sessions and visits -------------------------------------------------------------------


async def inbound(db: AsyncSession, customer: User) -> WhatsAppMessage:
    message = WhatsAppMessage(
        wa_message_id=f"wamid.{uuid.uuid4().hex}",
        direction="in",
        phone_e164=customer.phone_e164,
        message_type="text",
        body="Hey Notchs!, get me notched. KAHWA-482913",
        status="received",
    )
    db.add(message)
    await db.flush()
    return message


async def scan(
    db: AsyncSession,
    customer: User,
    venue: Venue | None,
    status: str = ScanStatus.VERIFIED,
    reject_reason: str | None = None,
) -> ScanSession:
    row = ScanSession(
        user_id=customer.id,
        venue_id=venue.id if venue else None,
        inbound_message_id=(await inbound(db, customer)).id,
        scan_code="KAHWA",
        code_submitted="482913",
        status=status,
        reject_reason=reject_reason,
        resolved_at=datetime.now(UTC),
    )
    db.add(row)
    await db.flush()
    return row


def visit(customer: User, venue: Venue, session: ScanSession, **overrides: object) -> Visit:
    data: dict[str, object] = {
        "user_id": customer.id,
        "venue_id": venue.id,
        "scan_session_id": session.id,
        "visit_date": date(2026, 10, 21),
        "visited_at": datetime.now(UTC),
        "notch_no": 1,
        "visit_no_at_venue": 1,
        "code_slot": 59_000_000,
    }
    return Visit(**(data | overrides))


async def test_scan_sessions_verified_or_rejected_with_a_reason(
    db, venue: Venue, customer: User
) -> None:
    for reason in RejectReason:
        await scan(db, customer, venue, ScanStatus.REJECTED, reason)
    unknown_venue = await scan(db, customer, None, ScanStatus.REJECTED, RejectReason.UNKNOWN_VENUE)
    assert unknown_venue.venue_id is None

    message = await inbound(db, customer)

    def session(**fields: object) -> ScanSession:
        return ScanSession(user_id=customer.id, inbound_message_id=message.id, **fields)

    assert await rejected(db, session(status="rejected"))  # no reason
    assert await rejected(db, session(status="verified", reject_reason="wrong_code"))
    assert await rejected(db, session(status="received"))  # no such state
    assert await rejected(db, session(status="rejected", reject_reason="bad_luck"))
    assert "phone" not in " ".join(ScanSession.__table__.columns.keys())


async def test_one_scan_session_per_message(db, venue: Venue, customer: User) -> None:
    first = await scan(db, customer, venue)

    again = ScanSession(
        user_id=customer.id, inbound_message_id=first.inbound_message_id, status="verified"
    )

    assert await rejected(db, again)  # Meta redelivering a message never makes a second scan


async def test_one_visit_per_customer_per_venue_per_day(db, venue: Venue, customer: User) -> None:
    first = visit(customer, venue, await scan(db, customer, venue))
    assert not await rejected(db, first)
    assert (first.status, first.verification_level) == ("valid", "basic")

    same_day = visit(
        customer, venue, await scan(db, customer, venue), notch_no=2, visit_no_at_venue=2
    )
    next_day = visit(
        customer,
        venue,
        await scan(db, customer, venue),
        notch_no=3,
        visit_no_at_venue=2,
        visit_date=date(2026, 10, 22),
    )

    assert await rejected(db, same_day)
    assert not await rejected(db, next_day)


async def test_one_visit_per_scan_session(db, venue: Venue, customer: User) -> None:
    session = await scan(db, customer, venue)
    assert not await rejected(db, visit(customer, venue, session))

    assert await rejected(
        db, visit(customer, venue, session, notch_no=2, visit_date=date(2026, 10, 23))
    )


async def test_notch_numbers_are_unique_per_venue(db, venue: Venue, customer: User) -> None:
    other = await find_or_create_customer(db, phone(), "Aman")
    assert not await rejected(
        db, visit(customer, venue, await scan(db, customer, venue), notch_no=7)
    )

    assert await rejected(db, visit(other, venue, await scan(db, other, venue), notch_no=7))


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "deleted"},
        {"verification_level": "gold"},
        {"notch_no": 0},
        {"visit_no_at_venue": 0},
    ],
    ids=["status", "verification-level", "notch-no", "visit-no"],
)
async def test_visit_checks(db, venue: Venue, customer: User, overrides: dict[str, object]) -> None:
    assert await rejected(db, visit(customer, venue, await scan(db, customer, venue), **overrides))


# --- points -------------------------------------------------------------------------------------


async def test_points_ledger(db, venue: Venue, customer: User) -> None:
    stamp = visit(customer, venue, await scan(db, customer, venue))
    db.add(stamp)
    await db.flush()
    for reason, points in ((PointsReason.VISIT, 10), (PointsReason.NEW_VENUE, 2)):
        db.add(PointsEntry(user_id=customer.id, visit_id=stamp.id, reason=reason, points=points))
    await db.flush()

    rows = (await db.scalars(select(PointsEntry).where(PointsEntry.user_id == customer.id))).all()

    assert sorted((r.reason, r.points) for r in rows) == [("new_venue", 2), ("visit", 10)]
    assert await rejected(db, PointsEntry(user_id=customer.id, reason="bonus", points=100))
    assert not await rejected(
        db, PointsEntry(user_id=customer.id, reason=PointsReason.ADJUSTMENT, points=-5)
    )
