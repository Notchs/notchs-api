"""What a scan means for the customer: consent, review flags, badges and the reply's facts.

The scan itself is checked and counted by `ScanService` (app/modules/scans/service.py):

    code right and current                  -> a visit with points: Notched (B2), or consent (B1)
    code too old, wrong, or no such venue   -> expired (B4)
    already notched this venue today        -> B3
    counter phone offline                   -> Notch flagged for review
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
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.locations.models import City
from app.modules.locations.time import local
from app.modules.notches import badges, signals
from app.modules.notches.models import Badge, NotchFlag, PendingCheckIn, SignalKind
from app.modules.scans.models import COUNTED_STATUSES, RejectReason, Visit, VisitStatus
from app.modules.scans.parser import Scan
from app.modules.scans.service import ScanResult, ScanService
from app.modules.users import founding
from app.modules.users.models import User
from app.modules.venues.counters import ONLINE_WITHIN
from app.modules.venues.models import CounterDevice, Venue, VenueStatus


class Outcome(StrEnum):
    NOTCHED = "notched"
    ALREADY_TODAY = "already_today"
    EXPIRED = "expired"  # old, wrong, or for a venue that does not exist
    VENUE_NOT_LIVE = "venue_not_live"  # paused, closed, or not set up yet
    NEEDS_CONSENT = "needs_consent"
    DAILY_LIMIT = "daily_limit"  # too many places today


_OUTCOME_OF_REFUSAL = {
    RejectReason.ALREADY_TODAY: Outcome.ALREADY_TODAY,
    RejectReason.VENUE_UNAVAILABLE: Outcome.VENUE_NOT_LIVE,
    RejectReason.DAILY_LIMIT: Outcome.DAILY_LIMIT,
}
# The code was not right: nothing here says anyone was at this counter.
_CODE_REFUSALS = (
    RejectReason.UNKNOWN_VENUE,
    RejectReason.VENUE_UNAVAILABLE,
    RejectReason.WRONG_CODE,
    RejectReason.EXPIRED_CODE,
    RejectReason.BLOCKED_CUSTOMER,
)
PENDING_FOR = timedelta(minutes=15)


@dataclass(slots=True)
class CheckInResult:
    outcome: Outcome
    venue: Venue | None = None
    notch: Visit | None = None
    # Counted Notchs at this venue this month, this one included (B2's next step).
    visits_this_month: int = 0
    new_badges: list[Badge] = field(default_factory=list)
    # Set on the customer's first ever Notch if they have a number (B8, sent once).
    announce_founding_no: int | None = None


class CheckInService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self.session = session
        self.redis = redis
        self.scans = ScanService(session, redis)

    async def scan(
        self, user: User, scan: Scan, *, scanned_at: datetime, whatsapp_message_id: uuid.UUID
    ) -> CheckInResult:
        result = await self.scans.record_scan(
            user, whatsapp_message_id, scan, now=scanned_at, consent_required=True
        )
        counter = result.counter
        offline = counter is not None and _is_offline(counter, scanned_at)
        if counter is not None and result.reject_reason not in _CODE_REFUSALS:
            # A real scan at this counter, whatever happens next (E3's live test, E5's last scan).
            if counter.last_scan_at is None or scanned_at > counter.last_scan_at:
                counter.last_scan_at = scanned_at
            await signals.count_scan(
                self.session,
                self.redis,
                venue_id=counter.venue_id,
                counter_id=counter.id,
                at=scanned_at,
            )

        if result.awaiting_consent:
            await self._keep_pending(user, result, scan, whatsapp_message_id, scanned_at, offline)
            return CheckInResult(Outcome.NEEDS_CONSENT, venue=result.venue)
        return await self._finish(user, result, offline)

    async def complete_pending(self, user: User) -> CheckInResult | None:
        """After the customer agreed (B1): turn their waiting scan into a Notch. None if
        nothing was waiting (or it waited too long). The row is locked and deleted in this
        transaction: two "I agree" taps make one Notch, and a failure leaves it waiting for the
        retry."""
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
        message_id = pending.message_id
        scan = Scan(pending.scan_code, pending.code)
        slot = pending.code_slot
        scanned_at = pending.scanned_at
        offline = pending.counter_offline
        venue_id = pending.venue_id
        counter_id = pending.counter_id
        await self.session.delete(pending)
        await self.session.flush()

        venue = await self.session.scalar(
            select(Venue).where(Venue.id == venue_id, Venue.deleted_at.is_(None))
        )
        counter = await self.session.get(CounterDevice, counter_id) if counter_id else None
        if venue is None or venue.status != VenueStatus.COUNTER_LIVE or counter is None:
            return CheckInResult(Outcome.VENUE_NOT_LIVE, venue=venue)
        result = await self.scans.count_checked(
            user, message_id, scan, venue, counter, slot, now=scanned_at
        )
        return await self._finish(user, result, offline)

    async def _keep_pending(
        self,
        user: User,
        result: ScanResult,
        scan: Scan,
        message_id: uuid.UUID,
        scanned_at: datetime,
        offline: bool,
    ) -> None:
        """One waiting scan per customer: a newer one replaces it."""
        assert result.venue is not None and result.counter is not None
        assert result.code_slot is not None
        values = {
            "venue_id": result.venue.id,
            "counter_id": result.counter.id,
            "message_id": message_id,
            "scan_code": scan.scan_code,
            "code": scan.live_code,
            "code_slot": result.code_slot,
            "scanned_at": scanned_at,
            "counter_offline": offline,
            "expires_at": datetime.now(UTC) + PENDING_FOR,
        }
        await self.session.execute(
            insert(PendingCheckIn)
            .values(user_id=user.id, **values)
            .on_conflict_do_update(index_elements=[PendingCheckIn.user_id], set_=values)
        )

    async def _finish(self, user: User, result: ScanResult, offline: bool) -> CheckInResult:
        venue = result.venue
        if result.visit is None:
            outcome = _OUTCOME_OF_REFUSAL.get(result.reject_reason, Outcome.EXPIRED)
            return CheckInResult(outcome, venue=venue)
        notch = result.visit
        if result.replayed:  # this message was already counted: no second badge or number
            return CheckInResult(Outcome.NOTCHED, venue=venue, notch=notch)
        assert venue is not None

        counter = result.counter
        tz = await self.session.scalar(select(City.timezone).where(City.id == venue.city_id))
        if offline and counter is not None:
            _flag(notch, NotchFlag.COUNTER_OFFLINE, "Counter phone was offline during this scan.")
            day = local(notch.visited_at, tz).date().isoformat()
            await signals.record(
                self.session,
                SignalKind.COUNTER_OFFLINE,
                f"{SignalKind.COUNTER_OFFLINE}:{counter.id}:{day}",
                venue_id=venue.id,
                counter_id=counter.id,
                at=notch.visited_at,
                details={"last_seen_at": _iso(counter.last_seen_at), "notch": notch.notch_code},
            )
        if counter is not None:
            await self._check_shared_code(user, venue, counter, notch)

        total_notches = await self.session.scalar(
            select(func.count()).where(Visit.user_id == user.id, Visit.status.in_(COUNTED_STATUSES))
        )
        number = await founding.assign(self.session, user)
        new_badges = await badges.award_after_notch(self.session, user, venue, notch)
        visits = await badges.visits_in_month(self.session, user.id, venue.id, notch.visit_date)
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
        self, user: User, venue: Venue, counter: CounterDevice, notch: Visit
    ) -> None:
        """The same code from another number: flag every Notch made with it."""
        others = (
            await self.session.scalars(
                select(Visit).where(
                    Visit.counter_id == counter.id,
                    Visit.code_slot == notch.code_slot,
                    Visit.id != notch.id,
                    Visit.user_id.is_distinct_from(user.id),
                    Visit.status != VisitStatus.REVOKED,
                )
            )
        ).all()
        if not others:
            return
        for other in others:
            gap = abs((notch.visited_at - other.visited_at).total_seconds())
            _flag(
                other,
                NotchFlag.SHARED_CODE,
                f"Same code also sent by another number {gap:.0f} s apart.",
            )
        _flag(
            notch, NotchFlag.SHARED_CODE, f"Same code also sent by {len(others)} other number(s)."
        )
        await signals.record(
            self.session,
            SignalKind.SHARED_CODE,
            f"{SignalKind.SHARED_CODE}:{counter.id}:{notch.code_slot}",
            venue_id=venue.id,
            counter_id=counter.id,
            at=notch.visited_at,
            details={
                "code": notch.code,
                "numbers": len(others) + 1,
                "notches": [n.notch_code for n in [*others, notch]],
                "seconds_apart": max(
                    abs((notch.visited_at - o.visited_at).total_seconds()) for o in others
                ),
            },
        )


def _is_offline(counter: CounterDevice, at: datetime) -> bool:
    return counter.last_seen_at is None or at - counter.last_seen_at > ONLINE_WITHIN


def _flag(visit: Visit, flag: NotchFlag, note: str) -> None:
    if flag not in visit.flags:
        visit.flags = [*visit.flags, flag]
    visit.flag_note = f"{visit.flag_note} {note}" if visit.flag_note else note
    if visit.status == VisitStatus.VALID:
        visit.status = VisitStatus.FLAGGED


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


async def delete_expired_pending(session: AsyncSession) -> int:
    """Scans that waited too long for consent: they will never be used."""
    result = await session.execute(
        delete(PendingCheckIn).where(PendingCheckIn.expires_at <= datetime.now(UTC))
    )
    return result.rowcount or 0
