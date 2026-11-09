"""Database reads and locks for bill-photo claims."""

import uuid
from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import Row, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin_users.models import AdminUser
from app.modules.claims.models import ClaimStatus, VisitClaim
from app.modules.locations.models import Area
from app.modules.scans.models import Visit
from app.modules.users.models import User
from app.modules.venues.models import CounterDevice, Venue, VenueStatus
from app.modules.whatsapp.models import MessageDirection, WhatsAppMessage

MAX_VENUE_RESULTS = 10


def _like(term: str) -> str:
    """`%term%` with LIKE's own characters taken literally."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class ClaimRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # The customer's side.

    async def live_venues(self, query: str) -> Sequence[Row[tuple[Venue, str | None]]]:
        """Places that are live for scans, by name."""
        result = await self.session.execute(
            select(Venue, Area.name)
            .outerjoin(Area, Area.id == Venue.area_id)
            .where(
                Venue.status == VenueStatus.COUNTER_LIVE,
                Venue.deleted_at.is_(None),
                Venue.name.ilike(_like(query), escape="\\"),
            )
            .order_by(Venue.name)
            .limit(MAX_VENUE_RESULTS)
        )
        return result.all()

    async def live_venue(self, venue_code: str) -> Venue | None:
        return await self.session.scalar(
            select(Venue).where(
                Venue.venue_code == venue_code,
                Venue.status == VenueStatus.COUNTER_LIVE,
                Venue.deleted_at.is_(None),
            )
        )

    async def count_started_on(self, user_id: uuid.UUID, day: date) -> int:
        return (
            await self.session.scalar(
                select(func.count())
                .select_from(VisitClaim)
                .where(VisitClaim.user_id == user_id, VisitClaim.visit_date == day)
            )
            or 0
        )

    async def live_claim(
        self, user_id: uuid.UUID, venue_id: uuid.UUID, day: date
    ) -> VisitClaim | None:
        """The one claim per customer, place and day that isn't rejected."""
        return await self.session.scalar(
            select(VisitClaim).where(
                VisitClaim.user_id == user_id,
                VisitClaim.venue_id == venue_id,
                VisitClaim.visit_date == day,
                VisitClaim.status != ClaimStatus.REJECTED,
            )
        )

    async def my_claims(
        self, user_id: uuid.UUID, limit: int
    ) -> Sequence[Row[tuple[VisitClaim, str, int | None]]]:
        result = await self.session.execute(
            select(VisitClaim, Venue.name, Visit.notch_no)
            .join(Venue, Venue.id == VisitClaim.venue_id)
            .outerjoin(Visit, Visit.id == VisitClaim.visit_id)
            .where(VisitClaim.user_id == user_id)
            .order_by(VisitClaim.created_at.desc())
            .limit(limit)
        )
        return result.all()

    # The bot's side.

    async def lock_awaiting(
        self, user_id: uuid.UUID, day: date, claim_code: str | None = None
    ) -> VisitClaim | None:
        """The customer's newest claim waiting for a photo today (or the one with this code)."""
        query = select(VisitClaim).where(
            VisitClaim.user_id == user_id,
            VisitClaim.visit_date == day,
            VisitClaim.status == ClaimStatus.AWAITING_PHOTO,
        )
        if claim_code is not None:
            query = query.where(VisitClaim.claim_code == claim_code)
        return await self.session.scalar(
            query.order_by(VisitClaim.created_at.desc()).limit(1).with_for_update()
        )

    async def claim_of(self, user_id: uuid.UUID, claim_code: str) -> VisitClaim | None:
        return await self.session.scalar(
            select(VisitClaim).where(
                VisitClaim.user_id == user_id, VisitClaim.claim_code == claim_code
            )
        )

    # The admin's side.

    def _admin_rows(self) -> Select[tuple[VisitClaim, Venue, str, str | None, int | None]]:
        return (
            select(VisitClaim, Venue, User.handle, AdminUser.name, Visit.notch_no)
            .join(Venue, Venue.id == VisitClaim.venue_id)
            .join(User, User.id == VisitClaim.user_id)
            .outerjoin(AdminUser, AdminUser.id == VisitClaim.reviewed_by_admin_id)
            .outerjoin(Visit, Visit.id == VisitClaim.visit_id)
        )

    async def admin_list(
        self, status: ClaimStatus | None, offset: int, limit: int
    ) -> tuple[Sequence[Row], int]:
        query = self._admin_rows()
        count = select(func.count()).select_from(VisitClaim)
        if status is not None:
            query = query.where(VisitClaim.status == status)
            count = count.where(VisitClaim.status == status)
        rows = await self.session.execute(
            query.order_by(VisitClaim.created_at, VisitClaim.id).offset(offset).limit(limit)
        )
        return rows.all(), await self.session.scalar(count) or 0

    async def admin_row(self, claim_code: str) -> Row | None:
        result = await self.session.execute(
            self._admin_rows().where(VisitClaim.claim_code == claim_code)
        )
        return result.first()

    async def lock_by_code(self, claim_code: str) -> VisitClaim | None:
        return await self.session.scalar(
            select(VisitClaim)
            .where(VisitClaim.claim_code == claim_code)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def counters_last_seen(
        self, venue_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, datetime | None]:
        """Each place's paired counter phone's last check-in."""
        if not venue_ids:
            return {}
        result = await self.session.execute(
            select(CounterDevice.venue_id, func.max(CounterDevice.last_seen_at))
            .where(
                CounterDevice.venue_id.in_(venue_ids),
                CounterDevice.revoked_at.is_(None),
                CounterDevice.token_hash.is_not(None),
            )
            .group_by(CounterDevice.venue_id)
        )
        return dict(result.tuples().all())

    async def last_message_from(self, phone_e164: str) -> datetime | None:
        """When this number last wrote to Notchs: replies to it are free for 24 hours after."""
        return await self.session.scalar(
            select(func.max(WhatsAppMessage.created_at)).where(
                WhatsAppMessage.phone_e164 == phone_e164,
                WhatsAppMessage.direction == MessageDirection.IN,
            )
        )
