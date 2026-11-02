"""The scan pipeline: one scan message becomes a visit with points, or a rejection with a reason.

    customer active -> venue exists -> venue live -> live code right and current
    -> not here already today -> under the venues-per-day limit -> code not overused
    -> visit + points

Every outcome is saved as one `scan_sessions` row. Nothing here commits: the caller owns the
transaction, so a failure keeps nothing and the message is retried. A retry of a message that was
already recorded returns the first result, so it can never count twice.

The customer row, then the venue row, are locked before the numbers are read. That keeps the daily
counts and the venue's notch numbers exact when several scans arrive at once.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import crypto
from app.common.time import ist_date
from app.core import rate_limit
from app.core.logging import get_logger
from app.modules.scans import verifier
from app.modules.scans.models import (
    PointsEntry,
    PointsReason,
    RejectReason,
    ScanSession,
    ScanStatus,
    Visit,
)
from app.modules.scans.parser import Scan
from app.modules.scans.repository import ScanRepository
from app.modules.users.models import User, UserStatus
from app.modules.venues.models import CounterDevice, Venue, VenueStatus

logger = get_logger(__name__)

MAX_VENUES_PER_DAY = 4
MAX_PEOPLE_PER_CODE = 5
# Failed guesses per customer, so nobody can try codes over WhatsApp until one works.
MAX_WRONG_CODES = 10
WRONG_CODE_WINDOW_SECONDS = 15 * 60

VISIT_POINTS = 10
NEW_VENUE_POINTS = 2
EVERY_THIRD_VISIT_POINTS = 2


def visit_points(visit_no_at_venue: int) -> list[tuple[PointsReason, int]]:
    """What the customer's Nth visit to a venue earns."""
    points = [(PointsReason.VISIT, VISIT_POINTS)]
    if visit_no_at_venue == 1:
        points.append((PointsReason.NEW_VENUE, NEW_VENUE_POINTS))
    if visit_no_at_venue % 3 == 0:
        points.append((PointsReason.EVERY_THIRD_VISIT, EVERY_THIRD_VISIT_POINTS))
    return points


@dataclass(frozen=True, slots=True)
class ScanResult:
    scan_session: ScanSession
    visit: Visit | None = None
    points_earned: int = 0

    @property
    def verified(self) -> bool:
        return self.scan_session.status == ScanStatus.VERIFIED

    @property
    def reject_reason(self) -> RejectReason | None:
        reason = self.scan_session.reject_reason
        return RejectReason(reason) if reason else None


class ScanService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self.session = session
        self.redis = redis
        self.repository = ScanRepository(session)

    async def record_scan(
        self,
        user: User,
        message_id: uuid.UUID,
        scan: Scan,
        *,
        now: datetime | None = None,
    ) -> ScanResult:
        now = now or datetime.now(UTC)
        # The lock below re-reads the customer, which would drop changes not yet saved.
        await self.session.flush()

        user = await self.repository.lock_customer(user.id)
        # Read under the lock, so the same message handled twice at once is still recorded once.
        existing = await self.repository.session_for_message(message_id)
        if existing is not None:
            return await self._earlier_result(existing)

        if user.status != UserStatus.ACTIVE or user.deleted_at is not None:
            return await self._reject(user, message_id, scan, RejectReason.BLOCKED_CUSTOMER, now)
        user.last_seen_at = now

        venue = await self.repository.venue_by_scan_code(scan.scan_code)
        if venue is None:
            return await self._reject(user, message_id, scan, RejectReason.UNKNOWN_VENUE, now)

        counter = await self.repository.paired_counter(venue.id)
        if counter is None or venue.status != VenueStatus.COUNTER_LIVE or venue.deleted_at:
            return await self._reject(
                user, message_id, scan, RejectReason.VENUE_UNAVAILABLE, now, venue, counter
            )

        slot, refusal = await self._check_code(user, counter, scan, now)
        if slot is None:
            assert refusal is not None
            return await self._reject(user, message_id, scan, refusal, now, venue, counter)

        today = ist_date(now)
        if await self.repository.has_visit_on(user.id, venue.id, today):
            return await self._reject(
                user, message_id, scan, RejectReason.ALREADY_TODAY, now, venue, counter
            )
        if await self.repository.count_valid_visits_on(user.id, today) >= MAX_VENUES_PER_DAY:
            return await self._reject(
                user, message_id, scan, RejectReason.DAILY_LIMIT, now, venue, counter
            )

        await self.repository.lock_venue(venue.id)
        code_lifetime = (verifier.SLOTS_BEFORE + verifier.SLOTS_AFTER + 1) * counter.code_interval_s
        since = now - timedelta(seconds=code_lifetime)
        if await self.repository.count_code_uses(venue.id, slot, since) >= MAX_PEOPLE_PER_CODE:
            return await self._reject(
                user, message_id, scan, RejectReason.CODE_USED, now, venue, counter
            )

        return await self._count_visit(user, message_id, scan, venue, counter, slot, now)

    async def _check_code(
        self, user: User, counter: CounterDevice, scan: Scan, now: datetime
    ) -> tuple[int | None, RejectReason | None]:
        """The code's time slot, or why it was refused. Only wrong codes use up the guesses, so
        a customer who pressed Send late is never locked out."""
        key = f"rl:scan_code:{user.id}"
        if await rate_limit.is_limited(self.redis, key, limit=MAX_WRONG_CODES):
            return None, RejectReason.WRONG_CODE

        secret = crypto.decrypt(counter.totp_secret_enc)
        interval = counter.code_interval_s
        timestamp = now.timestamp()
        slot = verifier.verify(secret, scan.live_code, interval=interval, now=timestamp)
        if slot is not None:
            return slot, None
        if verifier.is_expired(secret, scan.live_code, interval=interval, now=timestamp):
            return None, RejectReason.EXPIRED_CODE
        await rate_limit.hit(
            self.redis, key, limit=MAX_WRONG_CODES, window_seconds=WRONG_CODE_WINDOW_SECONDS
        )
        return None, RejectReason.WRONG_CODE

    async def _count_visit(
        self,
        user: User,
        message_id: uuid.UUID,
        scan: Scan,
        venue: Venue,
        counter: CounterDevice,
        slot: int,
        now: datetime,
    ) -> ScanResult:
        scan_session = self._session_row(
            user, message_id, scan, now, venue, counter, status=ScanStatus.VERIFIED
        )
        self.session.add(scan_session)
        await self.session.flush()

        visit_no = await self.repository.count_valid_visits_at(user.id, venue.id) + 1
        visit = Visit(
            user_id=user.id,
            venue_id=venue.id,
            scan_session_id=scan_session.id,
            visit_date=ist_date(now),
            visited_at=now,
            notch_no=await self.repository.last_notch_no(venue.id) + 1,
            visit_no_at_venue=visit_no,
            code_slot=slot,
        )
        self.session.add(visit)
        await self.session.flush()

        earned = visit_points(visit_no)
        self.session.add_all(
            PointsEntry(user_id=user.id, visit_id=visit.id, reason=reason, points=points)
            for reason, points in earned
        )
        if user.city_id is None:
            user.city_id = venue.city_id
        await self.session.flush()

        logger.info("Scan %s verified at venue %s", scan_session.id, venue.venue_code)
        return ScanResult(scan_session, visit, sum(points for _, points in earned))

    async def _reject(
        self,
        user: User,
        message_id: uuid.UUID,
        scan: Scan,
        reason: RejectReason,
        now: datetime,
        venue: Venue | None = None,
        counter: CounterDevice | None = None,
    ) -> ScanResult:
        scan_session = self._session_row(
            user, message_id, scan, now, venue, counter, status=ScanStatus.REJECTED, reason=reason
        )
        self.session.add(scan_session)
        await self.session.flush()
        logger.info("Scan %s rejected: %s", scan_session.id, reason.value)
        return ScanResult(scan_session)

    @staticmethod
    def _session_row(
        user: User,
        message_id: uuid.UUID,
        scan: Scan,
        now: datetime,
        venue: Venue | None,
        counter: CounterDevice | None,
        *,
        status: ScanStatus,
        reason: RejectReason | None = None,
    ) -> ScanSession:
        return ScanSession(
            user_id=user.id,
            venue_id=venue.id if venue else None,
            counter_device_id=counter.id if counter else None,
            inbound_message_id=message_id,
            scan_code=scan.scan_code,
            code_submitted=scan.live_code,
            status=status,
            reject_reason=reason,
            resolved_at=now,
        )

    async def _earlier_result(self, scan_session: ScanSession) -> ScanResult:
        visit = await self.repository.visit_for_session(scan_session.id)
        if visit is None:
            return ScanResult(scan_session)
        entries = await self.repository.points_for_visit(visit.id)
        return ScanResult(scan_session, visit, sum(entry.points for entry in entries))
