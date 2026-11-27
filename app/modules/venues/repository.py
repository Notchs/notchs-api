import uuid
from collections.abc import Sequence

from sqlalchemy import ColumnElement, Row, func, select

from app.common.repository import BaseRepository
from app.modules.locations.models import Area, City, CityStatus
from app.modules.venues.founding import next_founding_no
from app.modules.venues.models import Venue, VenueStatus


class VenueRepository(BaseRepository[Venue]):
    """Reads skip soft-deleted venues. Uniqueness checks include them, like the DB constraints."""

    model = Venue

    async def get(self, id_: uuid.UUID) -> Venue | None:
        return await self._one(Venue.id == id_)

    async def get_by_code(self, venue_code: str) -> Venue | None:
        return await self._one(Venue.venue_code == venue_code.upper())

    async def get_by_slug(self, city_id: uuid.UUID, slug: str) -> Venue | None:
        return await self._one(Venue.city_id == city_id, Venue.slug == slug)

    async def list(
        self,
        *,
        offset: int = 0,
        limit: int = 20,
        city_id: uuid.UUID | None = None,
        area_id: uuid.UUID | None = None,
        category: str | None = None,
        status: str | None = None,
    ) -> Sequence[Venue]:
        query = (
            select(Venue)
            .where(*self._filters(city_id, area_id, category, status))
            .order_by(Venue.name, Venue.id)
            .offset(offset)
            .limit(limit)
        )
        return (await self.session.scalars(query)).all()

    async def count(
        self,
        *,
        city_id: uuid.UUID | None = None,
        area_id: uuid.UUID | None = None,
        category: str | None = None,
        status: str | None = None,
    ) -> int:
        query = (
            select(func.count())
            .select_from(Venue)
            .where(*self._filters(city_id, area_id, category, status))
        )
        return await self.session.scalar(query) or 0

    async def slug_exists(
        self, city_id: uuid.UUID, slug: str, *, exclude_id: uuid.UUID | None = None
    ) -> bool:
        return await self._exists(
            Venue.city_id == city_id, Venue.slug == slug, exclude_id=exclude_id
        )

    async def scan_code_exists(
        self, scan_code: str, *, exclude_id: uuid.UUID | None = None
    ) -> bool:
        return await self._exists(Venue.scan_code == scan_code, exclude_id=exclude_id)

    async def founding_venue_no_exists(
        self, category: str, number: int, *, exclude_id: uuid.UUID | None = None
    ) -> bool:
        return await self._exists(
            Venue.category == category, Venue.founding_venue_no == number, exclude_id=exclude_id
        )

    async def next_founding_no(self, category: str) -> int | None:
        return await next_founding_no(self.session, category)

    async def get_city(self, city_id: uuid.UUID) -> City | None:
        return await self.session.get(City, city_id)

    async def get_area(self, area_id: uuid.UUID) -> Area | None:
        return await self.session.get(Area, area_id)

    async def get_live_city_by_slug(self, slug: str) -> City | None:
        query = select(City).where(City.slug == slug, City.status == CityStatus.LIVE)
        return (await self.session.scalars(query)).one_or_none()

    async def list_on_map(
        self, city_id: uuid.UUID, *, category: str | None, limit: int
    ) -> Sequence[Row]:
        """Places the public map may show: live, shown on Discover by their owner, located,
        not deleted. Only the columns a pin needs, with the area's name."""
        query = (
            select(
                Venue.venue_code,
                Venue.name,
                Venue.slug,
                Venue.category,
                Venue.latitude,
                Venue.longitude,
                Area.name.label("area"),
            )
            .outerjoin(Area, Area.id == Venue.area_id)
            .where(
                Venue.deleted_at.is_(None),
                Venue.city_id == city_id,
                Venue.status == VenueStatus.COUNTER_LIVE,
                Venue.visible_on_discover.is_(True),
                Venue.latitude.is_not(None),
                Venue.longitude.is_not(None),
            )
            .order_by(Venue.name, Venue.id)
            .limit(limit)
        )
        if category is not None:
            query = query.where(Venue.category == category)
        return (await self.session.execute(query)).all()

    async def _one(self, *conditions: ColumnElement[bool]) -> Venue | None:
        query = select(Venue).where(*conditions, Venue.deleted_at.is_(None))
        return (await self.session.scalars(query)).one_or_none()

    async def _exists(
        self, *conditions: ColumnElement[bool], exclude_id: uuid.UUID | None = None
    ) -> bool:
        query = select(Venue.id).where(*conditions).limit(1)
        if exclude_id is not None:
            query = query.where(Venue.id != exclude_id)
        return await self.session.scalar(query) is not None

    @staticmethod
    def _filters(
        city_id: uuid.UUID | None,
        area_id: uuid.UUID | None,
        category: str | None,
        status: str | None,
    ) -> list[ColumnElement[bool]]:
        filters: list[ColumnElement[bool]] = [Venue.deleted_at.is_(None)]
        if city_id is not None:
            filters.append(Venue.city_id == city_id)
        if area_id is not None:
            filters.append(Venue.area_id == area_id)
        if category is not None:
            filters.append(Venue.category == category)
        if status is not None:
            filters.append(Venue.status == status)
        return filters
