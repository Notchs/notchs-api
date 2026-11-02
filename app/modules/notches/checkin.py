"""Turning a counter scan into a Notch (QR spec, "Verification rules").

    code matches the current or previous window (or the next: phone clock a little ahead)
                                            -> Notched (B2), or consent first (B1)
    code older than that                    -> expired (B4)
    already notched this venue today        -> B3
    counter phone offline                   -> ± 2 windows accepted, Notch flagged for review
    same code from 2+ numbers               -> all of them notched and flagged for review
    more than 6 scans a minute at a counter -> a fraud signal for the pattern (G4)

A customer who has not agreed to the privacy notice yet (their first ever scan) is asked in
WhatsApp first (B1). Their checked scan waits in `pending_checkins` and becomes their first
Notch when they tap "I agree", even if the code has expired by then. The waiting row is used up
in the same transaction as the Notch, so a failure or retry never loses or doubles it.

Everything runs inside the WhatsApp processor's transaction: if anything fails, nothing is
kept and the message is retried.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from redis.asyncio import Redis
from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.common import crypto
from app.modules.locations.models import City
from app.modules.locations.time import local
from app.modules.notches import badges, signals
from app.modules.notches.codes import ScanMessage, match_offset, window_of
from app.modules.notches.models import (
    COUNTED_STATUSES,
    Badge,
    Notch,
    NotchFlag,
    NotchStatus,
    PendingCheckIn,
    SignalKind,
)
from app.modules.users import founding
from app.modules.users.models import User
from app.modules.venues.counters import ONLINE_WITHIN
from app.modules.venues.models import CounterDevice, Venue, VenueStatus

# Windows a code may belong to, relative to the scan's window, tried in this order.
ONLINE_OFFSETS = (0, -1, 1)  # current, previous (60 s grace), next (phone clock slightly ahead)
OFFLINE_OFFSETS = (0, -1, 1, -2, 2)  # QR spec: "Counter offline: checked against ± 2 windows"
PENDING_FOR = timedelta(minutes=15)


class Outcome(StrEnum):
    NOTCHED = "notched"
    ALREADY_TODAY = "already_today"
    EXPIRED = "expired"  # old, wrong, or for a counter that no longer exists
    VENUE_NOT_LIVE = "venue_not_live"  # paused, closed, or not set up yet
    NEEDS_CONSENT = "needs_consent"


@dataclass(slots=True)
class CheckInResult:
    outcome: Outcome
    venue: Venue | None = None
    notch: Notch | None = None
    # Counted Notchs at this venue this month, this one included (B2's next step).
    visits_this_month: int = 0
    new_badges: list[Badge] = field(default_factory=list)
    # Set on the customer's first ever Notch if they have a number (B8, sent once).
    announce_founding_no: int | None = None


@dataclass(frozen=True, slots=True)
class CheckedScan:
    """A scan whose code checked out."""

    venue_id: uuid.UUID
    counter_id: uuid.UUID | None
    code: str
    code_window: int
    code_offset: int
    scanned_at: datetime
    counter_offline: bool


class CheckInService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self.session = session
        self.redis = redis

    async def scan(
        self,
        user: User,
        message: ScanMessage,
        *,
        scanned_at: datetime,
        whatsapp_message_id: uuid.UUID | None = None,
    ) -> CheckInResult:
        venue = await self.session.scalar(
            select(Venue).where(Venue.venue_code == message.venue_code, Venue.deleted_at.is_(None))
        )
        if venue is None:
            return CheckInResult(Outcome.EXPIRED)
        if venue.status != VenueStatus.COUNTER_LIVE:
            return CheckInResult(Outcome.VENUE_NOT_LIVE, venue=venue)
        counter = await self._live_counter(venue.id)
        if counter is None:
            return CheckInResult(Outcome.EXPIRED, venue=venue)

        offline = counter.last_seen_at is None or scanned_at - counter.last_seen_at > ONLINE_WITHIN
        interval = counter.code_interval_s
        scan_window = window_of(scanned_at.timestamp(), interval)
        offset = match_offset(
            crypto.decrypt(counter.totp_secret_enc),
            message.code,
            scan_window,
            OFFLINE_OFFSETS if offline else ONLINE_OFFSETS,
        )
        if offset is None:
            return CheckInResult(Outcome.EXPIRED, venue=venue)

        # A real scan at this counter, whatever happens next (E3's live test, E5's last scan).
        if counter.last_scan_at is None or scanned_at > counter.last_scan_at:
            counter.last_scan_at = scanned_at
        await signals.count_scan(
            self.session, self.redis, venue_id=venue.id, counter_id=counter.id, at=scanned_at
        )

        checked = CheckedScan(
            venue_id=venue.id,
            counter_id=counter.id,
            code=message.code,
            code_window=scan_window + offset,
            code_offset=offset,
            scanned_at=scanned_at,
            counter_offline=offline,
        )
        if await self._already_today(user, venue, scanned_at):
            return CheckInResult(Outcome.ALREADY_TODAY, venue=venue)
        if user.consent_at is None:
            await self._keep_pending(user, checked)
            return CheckInResult(Outcome.NEEDS_CONSENT, venue=venue)
        return await self._notch(user, venue, counter, checked, whatsapp_message_id)

    async def complete_pending(self, user: User) -> CheckInResult | None:
        """After the customer agreed (B1): turn their waiting scan into a Notch. None if
        nothing was waiting (or it waited longer than PENDING_FOR). The row is locked and
        deleted in this transaction: two "I agree" taps make one Notch, and a failure leaves
        it waiting for the retry."""
        pending = await self.session.scalar(
            select(PendingCheckIn)
            .where(
                PendingCheckIn.user_id == user.id,
                PendingCheckIn.expires_at > datetime.now(UTC),
            )
            .with_for_update()
        )
        if pending is None:
            return None
        checked = CheckedScan(
            venue_id=pending.venue_id,
            counter_id=pending.counter_id,
            code=pending.code,
            code_window=pending.code_window,
            code_offset=pending.code_offset,
            scanned_at=pending.scanned_at,
            counter_offline=pending.counter_offline,
        )
        await self.session.delete(pending)
        await self.session.flush()
        venue = await self.session.scalar(
            select(Venue).where(Venue.id == checked.venue_id, Venue.deleted_at.is_(None))
        )
        if venue is None or venue.status != VenueStatus.COUNTER_LIVE:
            return CheckInResult(Outcome.VENUE_NOT_LIVE, venue=venue)
        counter = (
            await self.session.get(CounterDevice, checked.counter_id)
            if checked.counter_id
            else None
        )
        if await self._already_today(user, venue, checked.scanned_at):
            return CheckInResult(Outcome.ALREADY_TODAY, venue=venue)
        return await self._notch(user, venue, counter, checked, None)

    async def _keep_pending(self, user: User, checked: CheckedScan) -> None:
        """One waiting scan per customer: a newer one replaces it."""
        values = {
            "venue_id": checked.venue_id,
            "counter_id": checked.counter_id,
            "code": checked.code,
            "code_window": checked.code_window,
            "code_offset": checked.code_offset,
            "scanned_at": checked.scanned_at,
            "counter_offline": checked.counter_offline,
            "expires_at": datetime.now(UTC) + PENDING_FOR,
        }
        await self.session.execute(
            insert(PendingCheckIn)
            .values(user_id=user.id, **values)
            .on_conflict_do_update(index_elements=[PendingCheckIn.user_id], set_=values)
        )

    # --- internals ------------------------------------------------------------------------

    async def _live_counter(self, venue_id: uuid.UUID) -> CounterDevice | None:
        return await self.session.scalar(
            select(CounterDevice)
            .where(
                CounterDevice.venue_id == venue_id,
                CounterDevice.revoked_at.is_(None),
                CounterDevice.is_active.is_(True),
                CounterDevice.token_hash.is_not(None),
            )
            .with_for_update()
        )

    async def _timezone(self, venue: Venue) -> str:
        return await self.session.scalar(select(City.timezone).where(City.id == venue.city_id))

    async def _already_today(self, user: User, venue: Venue, scanned_at: datetime) -> bool:
        day = local(scanned_at, await self._timezone(venue)).date()
        return bool(
            await self.session.scalar(
                select(
                    exists().where(
                        Notch.user_id == user.id,
                        Notch.venue_id == venue.id,
                        Notch.local_date == day,
                        Notch.status != NotchStatus.REJECTED,
                    )
                )
            )
        )

    async def _notch(
        self,
        user: User,
        venue: Venue,
        counter: CounterDevice | None,
        checked: CheckedScan,
        whatsapp_message_id: uuid.UUID | None,
    ) -> CheckInResult:
        scanned_at = checked.scanned_at
        at_local = local(scanned_at, await self._timezone(venue))
        flags: list[str] = []
        notes: list[str] = []
        if checked.counter_offline:
            flags.append(NotchFlag.COUNTER_OFFLINE)
            notes.append("Counter phone was offline; an older code was accepted.")

        try:
            # A savepoint: a second message from the same customer racing this one hits the
            # one-per-day index, and only this part is undone.
            async with self.session.begin_nested():
                notch_no = await self.session.scalar(
                    update(Venue)
                    .where(Venue.id == venue.id)
                    .values(notch_count=Venue.notch_count + 1)
                    .returning(Venue.notch_count)
                    .execution_options(synchronize_session=False)
                )
                previous_visits = await self.session.scalar(
                    select(func.count()).where(
                        Notch.user_id == user.id,
                        Notch.venue_id == venue.id,
                        Notch.status.in_(COUNTED_STATUSES),
                    )
                )
                notch = Notch(
                    user_id=user.id,
                    venue_id=venue.id,
                    counter_id=counter.id if counter else None,
                    city_id=venue.city_id,
                    area_id=venue.area_id,
                    category=venue.category,
                    notch_no=notch_no,
                    user_visit_no=(previous_visits or 0) + 1,
                    scanned_at=scanned_at,
                    local_date=at_local.date(),
                    local_hour=at_local.hour,
                    code=checked.code,
                    code_window=checked.code_window,
                    code_offset=checked.code_offset,
                    status=NotchStatus.FLAGGED if flags else NotchStatus.VERIFIED,
                    flags=flags,
                    flag_note=" ".join(notes) or None,
                    whatsapp_message_id=whatsapp_message_id,
                )
                self.session.add(notch)
                await self.session.flush()
        except IntegrityError:
            return CheckInResult(Outcome.ALREADY_TODAY, venue=venue)
        set_committed_value(venue, "notch_count", notch_no)  # in step, without another UPDATE

        if checked.counter_offline and counter is not None:
            await signals.record(
                self.session,
                SignalKind.COUNTER_OFFLINE,
                f"{SignalKind.COUNTER_OFFLINE}:{counter.id}:{at_local.date().isoformat()}",
                venue_id=venue.id,
                counter_id=counter.id,
                at=scanned_at,
                details={"last_seen_at": _iso(counter.last_seen_at), "notch": notch.notch_code},
            )
        if counter is not None:
            await self._check_shared_code(user, venue, counter, notch)

        user.last_seen_at = datetime.now(UTC)
        if user.city_id is None:
            user.city_id = venue.city_id
        total_notches = await self.session.scalar(
            select(func.count()).where(Notch.user_id == user.id, Notch.status.in_(COUNTED_STATUSES))
        )
        number = await founding.assign(self.session, user)
        new_badges = await badges.award_after_notch(self.session, user, venue, notch)
        visits = await badges.visits_in_month(self.session, user.id, venue.id, notch.local_date)
        await self.session.flush()
        return CheckInResult(
            Outcome.NOTCHED,
            venue=venue,
            notch=notch,
            visits_this_month=visits,
            new_badges=new_badges,
            announce_founding_no=number if total_notches == 1 else None,
        )

    async def _check_shared_code(
        self, user: User, venue: Venue, counter: CounterDevice, notch: Notch
    ) -> None:
        """The same code from another number: flag every Notch made with it."""
        others = (
            await self.session.scalars(
                select(Notch).where(
                    Notch.counter_id == counter.id,
                    Notch.code_window == notch.code_window,
                    Notch.id != notch.id,
                    Notch.user_id.is_distinct_from(user.id),
                    Notch.status != NotchStatus.REJECTED,
                )
            )
        ).all()
        if not others:
            return
        for other in [*others, notch]:
            gap = abs((notch.scanned_at - other.scanned_at).total_seconds())
            if NotchFlag.SHARED_CODE not in other.flags:
                other.flags = [*other.flags, NotchFlag.SHARED_CODE]
            if other is not notch:
                note = f"Same code also sent by another number {gap:.0f} s apart."
                other.flag_note = _join(other.flag_note, note)
            if other.status == NotchStatus.VERIFIED:
                other.status = NotchStatus.FLAGGED
        notch.flag_note = _join(
            notch.flag_note, f"Same code also sent by {len(others)} other number(s)."
        )
        await signals.record(
            self.session,
            SignalKind.SHARED_CODE,
            f"{SignalKind.SHARED_CODE}:{counter.id}:{notch.code_window}",
            venue_id=venue.id,
            counter_id=counter.id,
            at=notch.scanned_at,
            details={
                "code": notch.code,
                "numbers": len(others) + 1,
                "notches": [n.notch_code for n in [*others, notch]],
                "seconds_apart": max(
                    abs((notch.scanned_at - o.scanned_at).total_seconds()) for o in others
                ),
            },
        )


def _join(existing: str | None, note: str) -> str:
    return f"{existing} {note}" if existing else note


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


async def delete_expired_pending(session: AsyncSession) -> int:
    """Scans that waited longer than PENDING_FOR for consent: they will never be used."""
    result = await session.execute(
        delete(PendingCheckIn).where(PendingCheckIn.expires_at <= datetime.now(UTC))
    )
    return result.rowcount or 0
