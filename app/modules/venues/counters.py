"""The venue's counter phone: the spare phone at the counter that shows the rotating QR code.

One counter per venue for now (a partial unique index enforces it). The flow:

    owner: POST /venue/counter/pairing-link   -> 10-minute single-use link (creates the counter
                                                 the first time)
    phone: POST /display/pair {token}         -> long-lived device cookie `nx_device`; a fresh
                                                 TOTP secret, so a replaced phone's codes stop
    phone: GET  /display/config               -> venue, label, TOTP secret and interval; the
                                                 phone makes the codes itself, offline
    phone: POST /display/heartbeat            -> "I'm on", shown to the owner

The device cookie only works on /display/*, never on the dashboard.
"""

import base64
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import crypto
from app.core import rate_limit
from app.core.config import settings
from app.modules.auth.enums import SubjectType, TokenType
from app.modules.auth.exceptions import TokenInvalidException
from app.modules.auth.one_time_tokens import OneTimeTokenService
from app.modules.auth.sessions import Subject, active_venue_member
from app.modules.auth.tokens import hash_token, lifetime, new_token
from app.modules.locations.models import Area, City
from app.modules.locations.time import today
from app.modules.notches.codes import message_template
from app.modules.notches.models import COUNTED_STATUSES, Notch
from app.modules.venues.counter_schemas import (
    CounterOut,
    DisplayConfigOut,
    DisplayQrOut,
    DisplayVenueOut,
    HeartbeatIn,
    HeartbeatOut,
    PairingLinkOut,
    TotpSettingsOut,
)
from app.modules.venues.exceptions import CounterNotFoundException, CounterRateLimitedException
from app.modules.venues.models import CounterDevice, Venue, VenueMember, VenueStatus

TOTP_SECRET_BYTES = 20  # 160 bits, the RFC 4226 recommendation
ONLINE_WITHIN = timedelta(minutes=2)
HEARTBEAT_EVERY = timedelta(minutes=1)  # last_seen_at is written at most this often
MAX_PAIRING_LINKS_PER_HOUR = 10


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(TOTP_SECRET_BYTES)).decode().rstrip("=")


def pairing_url(raw_token: str) -> str:
    return f"{settings.VENUE_APP_URL}/display/pair?token={raw_token}"


def counter_out(counter: CounterDevice, now: datetime | None = None) -> CounterOut:
    now = now or datetime.now(UTC)
    paired = counter.token_hash is not None
    return CounterOut(
        id=counter.id,
        label=counter.label,
        paired=paired,
        paired_at=counter.paired_at,
        last_seen_at=counter.last_seen_at,
        online=paired
        and counter.last_seen_at is not None
        and now - counter.last_seen_at <= ONLINE_WITHIN,
        code_interval_s=counter.code_interval_s,
        last_scan_at=counter.last_scan_at,
        battery_pct=counter.battery_pct,
        is_charging=counter.is_charging,
    )


@dataclass(frozen=True, slots=True)
class PairedDevice:
    counter: CounterDevice
    venue: Venue
    device_token: str  # raw, for the cookie; only exists right after pairing


class CounterService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self.session = session
        self.redis = redis
        self.tokens = OneTimeTokenService(session)

    # --- owner side ------------------------------------------------------------------

    async def get(self, member: VenueMember) -> CounterDevice:
        counter = await self._active(member.venue_id)
        if counter is None:
            raise CounterNotFoundException()
        return counter

    async def pairing_link(self, member: VenueMember, *, ip: str | None) -> PairingLinkOut:
        """A link for the counter phone. Creates the venue's counter the first time; after that
        the same counter is re-paired (the old phone keeps working until the new one pairs)."""
        if not await rate_limit.hit(
            self.redis,
            f"rl:pairing:{member.venue_id}",
            limit=MAX_PAIRING_LINKS_PER_HOUR,
            window_seconds=3600,
        ):
            raise CounterRateLimitedException()
        if await self._active(member.venue_id) is None:
            self.session.add(
                CounterDevice(
                    venue_id=member.venue_id, totp_secret_enc=crypto.encrypt(new_totp_secret())
                )
            )
            await self.session.flush()
        subject = Subject(SubjectType.VENUE_MEMBER, member.id, venue_id=member.venue_id)
        raw = await self.tokens.issue(subject, TokenType.DEVICE_PAIRING, ip=ip)
        return PairingLinkOut(
            url=pairing_url(raw),
            expires_at=datetime.now(UTC) + lifetime(TokenType.DEVICE_PAIRING),
        )

    async def rename(self, member: VenueMember, label: str) -> CounterDevice:
        counter = await self.get(member)
        counter.label = label
        await self.session.flush()
        await self.session.refresh(counter)
        return counter

    async def unpair(self, member: VenueMember) -> None:
        """Remove the counter. Its phone stops working on its next call; the row stays as
        history and the next pairing link starts a new counter."""
        counter = await self.get(member)
        counter.revoked_at = datetime.now(UTC)
        counter.is_active = False
        counter.token_hash = None
        await self.session.flush()

    # --- phone side ------------------------------------------------------------------

    async def pair(self, raw_token: str) -> PairedDevice:
        token = await self.tokens.consume(
            raw_token, (TokenType.DEVICE_PAIRING,), SubjectType.VENUE_MEMBER
        )
        member = (
            await active_venue_member(self.session, token.venue_member_id)
            if token.venue_member_id
            else None
        )
        counter = await self._active(member.venue_id, for_update=True) if member else None
        if member is None or counter is None:
            raise TokenInvalidException()

        now = datetime.now(UTC)
        device_token = new_token()
        counter.token_hash = hash_token(device_token)
        # A fresh secret every pairing: a replaced phone's codes stop being valid.
        counter.totp_secret_enc = crypto.encrypt(new_totp_secret())
        counter.paired_by_member_id = member.id
        counter.paired_at = now
        counter.last_seen_at = now
        counter.is_active = True

        venue = await self.session.get(Venue, counter.venue_id)
        assert venue is not None  # FK
        if venue.status == VenueStatus.SIGNED:
            venue.status = VenueStatus.COUNTER_LIVE  # the first pairing puts the venue live
        await self.session.flush()
        await self.session.refresh(counter)
        await self.session.refresh(venue)
        return PairedDevice(counter=counter, venue=venue, device_token=device_token)

    async def device(self, raw_device_token: str) -> tuple[CounterDevice, Venue] | None:
        """The paired counter and its venue, or None if the token is unknown or removed."""
        row = (
            await self.session.execute(
                select(CounterDevice, Venue)
                .join(Venue, Venue.id == CounterDevice.venue_id)
                .where(
                    CounterDevice.token_hash == hash_token(raw_device_token),
                    CounterDevice.revoked_at.is_(None),
                    CounterDevice.is_active.is_(True),
                    Venue.deleted_at.is_(None),
                )
            )
        ).one_or_none()
        return (row[0], row[1]) if row else None

    async def heartbeat(self, counter: CounterDevice, status: HeartbeatIn | None = None) -> None:
        now = datetime.now(UTC)
        changed = False
        if counter.last_seen_at is None or now - counter.last_seen_at >= HEARTBEAT_EVERY:
            counter.last_seen_at = now
            changed = True
        if status is not None and "battery_pct" in status.model_fields_set:
            counter.battery_pct = status.battery_pct
            changed = True
        if status is not None and "is_charging" in status.model_fields_set:
            counter.is_charging = status.is_charging
            changed = True
        if changed:
            # Back on: the next outage gets its own "Counter phone is off" email.
            counter.offline_alerted_at = None
            await self.session.flush()

    async def notched_today(self, venue: Venue) -> int:
        """Counted Notchs at the venue today, in its city's day ("14 notched here today")."""
        tz = await self.session.scalar(select(City.timezone).where(City.id == venue.city_id))
        return (
            await self.session.scalar(
                select(func.count()).where(
                    Notch.venue_id == venue.id,
                    Notch.local_date == today(tz),
                    Notch.status.in_(COUNTED_STATUSES),
                )
            )
            or 0
        )

    async def heartbeat_out(self, venue: Venue) -> HeartbeatOut:
        return HeartbeatOut(
            notched_today=await self.notched_today(venue), server_time=datetime.now(UTC)
        )

    async def display_config(self, counter: CounterDevice, venue: Venue) -> DisplayConfigOut:
        area = (
            await self.session.scalar(select(Area.name).where(Area.id == venue.area_id))
            if venue.area_id
            else None
        )
        return DisplayConfigOut(
            counter_id=counter.id,
            label=counter.label,
            venue=DisplayVenueOut(
                name=venue.name, venue_code=venue.venue_code, slug=venue.slug, area=area
            ),
            totp=TotpSettingsOut(
                secret=crypto.decrypt(counter.totp_secret_enc),
                interval_s=counter.code_interval_s,
            ),
            qr=DisplayQrOut(
                whatsapp_number=settings.WHATSAPP_PUBLIC_NUMBER.removeprefix("+"),
                message_template=message_template(venue.name, venue.venue_code),
            ),
            notched_today=await self.notched_today(venue),
            server_time=datetime.now(UTC),
        )

    async def _active(self, venue_id, *, for_update: bool = False) -> CounterDevice | None:
        query = select(CounterDevice).where(
            CounterDevice.venue_id == venue_id, CounterDevice.revoked_at.is_(None)
        )
        return await self.session.scalar(query.with_for_update() if for_update else query)
