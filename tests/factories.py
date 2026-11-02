"""Rows for database tests: a live venue with a paired counter phone, customers, and the
counter's codes. Everything gets sensible defaults; pass what a test cares about."""

import itertools
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.common import crypto
from app.common.ids import new_uuid
from app.modules.admin_users.models import AdminRole, AdminUser
from app.modules.auth.access_tokens import create_access_token
from app.modules.auth.cookies import access_cookie_name
from app.modules.auth.enums import SubjectType
from app.modules.locations.models import Area, City
from app.modules.scans.verifier import code_at as totp_at
from app.modules.scans.verifier import slot_of
from app.modules.users.models import User
from app.modules.venues.models import (
    CounterDevice,
    Venue,
    VenueCategory,
    VenueMember,
    VenueStatus,
)
from app.modules.whatsapp.models import (
    MessageDirection,
    MessageStatus,
    MessageType,
    WhatsAppMessage,
)

# A fixed base32 secret, so tests can make valid codes.
SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
_numbers = itertools.count(1)


async def city(session: AsyncSession, *, slug: str = "ahmedabad") -> City:
    row = City(name=slug.title(), slug=slug, state="Gujarat", is_active=True)
    session.add(row)
    await session.flush()
    return row


async def area(session: AsyncSession, city_row: City, *, name: str = "Navrangpura") -> Area:
    row = Area(city_id=city_row.id, name=name, slug=name.lower().replace(" ", "-"))
    session.add(row)
    await session.flush()
    return row


async def venue(
    session: AsyncSession,
    city_row: City,
    *,
    name: str | None = None,
    category: VenueCategory = VenueCategory.CAFE,
    area_row: Area | None = None,
    status: VenueStatus = VenueStatus.COUNTER_LIVE,
    **extra: object,
) -> Venue:
    number = next(_numbers)
    name = name or f"Venue {number}"
    row = Venue(
        city_id=city_row.id,
        area_id=area_row.id if area_row else None,
        name=name,
        slug=f"venue-{number}",
        category=category,
        status=status,
        **extra,
    )
    session.add(row)
    await session.flush()
    return row


async def counter(
    session: AsyncSession, venue_row: Venue, *, last_seen: datetime | None = None
) -> CounterDevice:
    row = CounterDevice(
        venue_id=venue_row.id,
        totp_secret_enc=crypto.encrypt(SECRET),
        token_hash=new_uuid().hex,
        paired_at=datetime.now(UTC),
        last_seen_at=last_seen or datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


async def user(
    session: AsyncSession,
    *,
    consented: bool = True,
    name: str | None = None,
    phone_e164: str | None = None,
    **extra: object,
) -> User:
    number = next(_numbers)
    row = User(
        phone_e164=phone_e164 or f"+9198{number:08d}",
        name=name,
        consent_at=datetime.now(UTC) if consented else None,
        **extra,
    )
    session.add(row)
    await session.flush()
    return row


def code_at(moment: datetime, *, offset: int = 0, interval: int = 30) -> str:
    """The counter's code for the window `offset` steps from `moment`'s."""
    return totp_at(SECRET, slot_of(moment.timestamp(), interval) + offset)


def ago(**kwargs: float) -> datetime:
    return datetime.now(UTC) - timedelta(**kwargs)


async def member(session: AsyncSession, venue_row: Venue, *, name: str = "Owner") -> VenueMember:
    number = next(_numbers)
    row = VenueMember(
        venue_id=venue_row.id,
        phone_e164=f"+9197{number:08d}",
        name=name,
        email=f"owner{number}@example.com",
    )
    session.add(row)
    await session.flush()
    return row


async def admin(session: AsyncSession, *, role: AdminRole = AdminRole.ADMIN) -> AdminUser:
    number = next(_numbers)
    row = AdminUser(email=f"admin{number}@notchs.com", name=f"Admin {number}", role=role)
    session.add(row)
    await session.flush()
    return row


def login(account: User | VenueMember | AdminUser) -> dict[str, str]:
    """The access cookie for this account, as the login endpoints would set it."""
    if isinstance(account, User):
        subject, venue_id = SubjectType.USER, None
    elif isinstance(account, VenueMember):
        subject, venue_id = SubjectType.VENUE_MEMBER, account.venue_id
    else:
        subject, venue_id = SubjectType.ADMIN, None
    token = create_access_token(subject, account.id, venue_id=venue_id)
    return {access_cookie_name(subject): token}


async def message_id(session: AsyncSession):
    """A stored inbound message for a scan to come from: every scan session has one."""
    row = WhatsAppMessage(
        direction=MessageDirection.IN,
        phone_e164="+919800000000",
        message_type=MessageType.TEXT,
        status=MessageStatus.RECEIVED,
        wa_message_id=f"wamid.test.{new_uuid().hex}",
    )
    session.add(row)
    await session.flush()
    return row.id


async def notch_at(
    session: AsyncSession, redis: object, user_row: User, venue_row: Venue, moment: datetime
):
    """A real check-in through the service, at `moment`."""
    from app.modules.notches.checkin import CheckInService
    from app.modules.scans.parser import Scan

    result = await CheckInService(session, redis).scan(  # type: ignore[arg-type]
        user_row,
        Scan(venue_row.scan_code, code_at(moment)),
        scanned_at=moment,
        whatsapp_message_id=await message_id(session),
    )
    assert result.notch is not None, result.outcome
    return result.notch
