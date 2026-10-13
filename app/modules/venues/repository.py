import uuid
from collections.abc import Sequence

from sqlalchemy import ColumnElement, func, select

from app.common.repository import BaseRepository
from app.modules.locations.models import Area, City
from app.modules.venues.models import Venue


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

    async def founding_venue_no_exists(
        self, number: int, *, exclude_id: uuid.UUID | None = None
    ) -> bool:
        return await self._exists(Venue.founding_venue_no == number, exclude_id=exclude_id)

    async def get_city(self, city_id: uuid.UUID) -> City | None:
        return await self.session.get(City, city_id)

    async def get_area(self, area_id: uuid.UUID) -> Area | None:
        return await self.session.get(Area, area_id)

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
