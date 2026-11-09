"""The Notchs team's admin panel: real totals, venues, Notchs and customers.

Changing a venue's status or a customer's status needs the `admin` role; reviewers can look at
everything and remove fraudulent Notchs. Every change is logged with the admin's id.
"""

from datetime import UTC, datetime, time, timedelta

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.dependencies import PaginationParams
from app.common.schemas import PaginatedResponse
from app.common.time import IST, ist_date
from app.core.logging import get_logger
from app.modules.admin_panel.exceptions import (
    AdminCounterNotPairedException,
    AdminCustomerNotFoundException,
    AdminNotchNotFoundException,
    AdminStatusChangeNotAllowedException,
    AdminVenueNotFoundException,
)
from app.modules.admin_panel.repository import AdminPanelRepository
from app.modules.admin_panel.schemas import (
    AdminCustomerRow,
    AdminNotchRow,
    AdminVenueRow,
    CustomerStatusTarget,
    OverviewOut,
    VenueStatusTarget,
)
from app.modules.admin_users.models import AdminUser
from app.modules.leaderboards.service import LeaderboardService
from app.modules.scans.models import Visit, VisitStatus
from app.modules.users.models import User, UserStatus
from app.modules.venues.counters import ONLINE_WITHIN
from app.modules.venues.models import Venue, VenueStatus

logger = get_logger(__name__)

# Which statuses an admin may move a venue to, from each status. A closed venue stays closed;
# going live needs a paired counter phone (pairing normally does it).
ALLOWED_VENUE_CHANGES: dict[str, set[VenueStatusTarget]] = {
    VenueStatus.PENDING: {VenueStatusTarget.CLOSED},
    VenueStatus.SIGNED: {VenueStatusTarget.PAUSED, VenueStatusTarget.CLOSED},
    VenueStatus.COUNTER_LIVE: {VenueStatusTarget.PAUSED, VenueStatusTarget.CLOSED},
    VenueStatus.PAUSED: {VenueStatusTarget.LIVE, VenueStatusTarget.CLOSED},
    VenueStatus.CLOSED: set(),
}


class AdminPanelService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self.redis = redis
        self.repository = AdminPanelRepository(session)
        self.session = session

    async def overview(self, now: datetime | None = None) -> OverviewOut:
        now = now or datetime.now(UTC)
        today = ist_date(now)
        week_ago = today - timedelta(days=6)
        counts = await self.repository.overview_counts(
            today,
            week_ago,
            datetime.combine(today, time(), IST),
            datetime.combine(week_ago, time(), IST),
        )
        return OverviewOut(as_of=now, **counts)

    async def venues(
        self, pagination: PaginationParams, *, status: str | None, q: str | None
    ) -> PaginatedResponse[AdminVenueRow]:
        now = datetime.now(UTC)
        rows, total = await self.repository.venues(
            status=status,
            q=q,
            week_ago=ist_date(now) - timedelta(days=6),
            offset=pagination.offset,
            limit=pagination.size,
        )
        items = [
            AdminVenueRow(
                venue_code=row.Venue.venue_code,
                name=row.Venue.name,
                category=row.Venue.category,
                area=row.area,
                status=row.Venue.status,
                joined_at=row.Venue.created_at,
                counter_paired=bool(row.paired),
                counter_online=bool(row.paired)
                and row.last_seen_at is not None
                and now - row.last_seen_at <= ONLINE_WITHIN,
                counter_last_seen_at=row.last_seen_at,
                notchs_total=row.notchs_total,
                notchs_7d=row.notchs_7d,
            )
            for row in rows
        ]
        return PaginatedResponse.build(items, total, pagination.page, pagination.size)

    async def set_venue_status(
        self, admin: AdminUser, venue_code: str, target: VenueStatusTarget
    ) -> Venue:
        venue = await self.repository.venue_by_code(venue_code, for_update=True)
        if venue is None:
            raise AdminVenueNotFoundException()
        if venue.status == target:
            return venue  # a double click: already done
        if target not in ALLOWED_VENUE_CHANGES.get(venue.status, set()):
            raise AdminStatusChangeNotAllowedException()
        if (
            target is VenueStatusTarget.LIVE
            and await self.repository.paired_counter(venue.id) is None
        ):
            raise AdminCounterNotPairedException()
        before = venue.status
        venue.status = target
        await self.session.flush()
        logger.info(
            "Admin %s changed venue %s from %s to %s", admin.id, venue.venue_code, before, target
        )
        return venue

    async def notchs(
        self, pagination: PaginationParams, *, venue_code: str | None, status: str | None
    ) -> PaginatedResponse[AdminNotchRow]:
        rows, total = await self.repository.notchs(
            venue_code=venue_code,
            status=status,
            offset=pagination.offset,
            limit=pagination.size,
        )
        items = [
            _notch_row(row.Visit, row.venue_code, row.venue_name, row.handle, row.points)
            for row in rows
        ]
        return PaginatedResponse.build(items, total, pagination.page, pagination.size)

    async def revoke(
        self, admin: AdminUser, venue_code: str, notch_no: int, reason: str
    ) -> AdminNotchRow:
        """Remove a fraudulent Notch: it stops counting for ranks, rewards and the venue's
        numbers. Doing it twice changes nothing."""
        venue = await self.repository.venue_by_code(venue_code)
        if venue is None:
            raise AdminNotchNotFoundException()
        visit = await self.repository.notch(venue.id, notch_no)
        if visit is None:
            raise AdminNotchNotFoundException()
        points = await self.repository.visit_points(visit.id)
        if visit.status != VisitStatus.REVOKED:
            visit.status = VisitStatus.REVOKED
            await self.session.flush()
            logger.info(
                "Admin %s removed Notch %s at venue %s (%s)",
                admin.id,
                notch_no,
                venue.venue_code,
                reason,
            )
            # Ranks are rebuilt from the database every hour; take the points off now too.
            if visit.user_id is not None:
                try:
                    await self.session.flush()  # the board is read back from the database
                    await LeaderboardService(self.redis).sync_user(self.session, visit.user_id)
                except RedisError:
                    logger.exception("Notch %s removed; ranks fix at the next rebuild", notch_no)
        user = await self.session.get(User, visit.user_id) if visit.user_id else None
        handle = user.handle if user else None
        return _notch_row(visit, venue.venue_code, venue.name, handle, points)

    async def customers(
        self, pagination: PaginationParams, *, status: str | None, q: str | None
    ) -> PaginatedResponse[AdminCustomerRow]:
        rows, total = await self.repository.customers(
            status=status, q=q, offset=pagination.offset, limit=pagination.size
        )
        items = [
            AdminCustomerRow(
                handle=row.User.handle,
                name=row.User.name,
                phone_last4=f"…{row.User.phone_e164[-4:]}",
                city=row.city,
                status=row.User.status,
                founding_member_no=row.User.founding_member_no,
                notchs=row.notchs,
                joined_at=row.User.created_at,
                last_seen_at=row.User.last_seen_at,
            )
            for row in rows
        ]
        return PaginatedResponse.build(items, total, pagination.page, pagination.size)

    async def set_customer_status(
        self, admin: AdminUser, handle: str, target: CustomerStatusTarget
    ) -> None:
        user = await self.repository.customer_by_handle(handle)
        if user is None or user.status == UserStatus.DELETED:
            raise AdminCustomerNotFoundException()
        if user.status == target:
            return
        user.status = target
        await self.session.flush()
        logger.info("Admin %s set customer %s to %s", admin.id, user.id, target)


def _notch_row(
    visit: Visit, venue_code: str, venue_name: str, handle: str | None, points: int
) -> AdminNotchRow:
    return AdminNotchRow(
        venue_code=venue_code,
        venue_name=venue_name,
        notch_no=visit.notch_no,
        customer_handle=handle,
        visit_date=visit.visit_date,
        visited_at=visit.visited_at,
        visit_no_at_venue=visit.visit_no_at_venue,
        status=visit.status,
        points=points,
    )
