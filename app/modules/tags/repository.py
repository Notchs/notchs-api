"""Data access for tags and the tables that use them."""

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert

from app.common.repository import BaseRepository
from app.modules.tags.models import Tag, TagKind, VenueItem, VenueTag, VisitTag
from app.modules.venues.models import Venue


class TagRepository(BaseRepository[Tag]):
    model = Tag

    async def list_by_kind(
        self, kind: TagKind | None, *, offset: int, limit: int
    ) -> tuple[Sequence[Tag], int]:
        query = select(Tag)
        counted = select(func.count()).select_from(Tag)
        if kind is not None:
            query = query.where(Tag.kind == kind)
            counted = counted.where(Tag.kind == kind)
        rows = await self.session.scalars(query.order_by(Tag.name).offset(offset).limit(limit))
        return rows.all(), await self.session.scalar(counted) or 0

    async def by_ids(self, ids: Sequence[uuid.UUID]) -> Sequence[Tag]:
        return (await self.session.scalars(select(Tag).where(Tag.id.in_(ids)))).all()

    async def is_answered(self, tag_id: uuid.UUID) -> bool:
        return bool(await self.session.scalar(select(exists().where(VisitTag.tag_id == tag_id))))

    async def is_used(self, tag_id: uuid.UUID) -> bool:
        if await self.is_answered(tag_id):
            return True
        for table in (VenueTag, VenueItem):
            if await self.session.scalar(select(exists().where(table.tag_id == tag_id))):
                return True
        return False

    async def lock_venue(self, venue_id: uuid.UUID) -> None:
        """So two saves at once cannot go over the limits or mix their lists."""
        await self.session.execute(select(Venue.id).where(Venue.id == venue_id).with_for_update())

    async def venue_tags(self, venue_id: uuid.UUID) -> Sequence[Tag]:
        rows = await self.session.scalars(
            select(Tag)
            .join(VenueTag, VenueTag.tag_id == Tag.id)
            .where(VenueTag.venue_id == venue_id)
            .order_by(Tag.name)
        )
        return rows.all()

    async def venue_items(self, venue_id: uuid.UUID) -> Sequence[Tag]:
        rows = await self.session.scalars(
            select(Tag)
            .join(VenueItem, VenueItem.tag_id == Tag.id)
            .where(VenueItem.venue_id == venue_id)
            .order_by(VenueItem.position)
        )
        return rows.all()

    async def venue_item(self, venue_id: uuid.UUID, tag_id: uuid.UUID) -> Tag | None:
        return await self.session.scalar(
            select(Tag)
            .join(VenueItem, VenueItem.tag_id == Tag.id)
            .where(VenueItem.venue_id == venue_id, VenueItem.tag_id == tag_id)
        )

    async def replace_venue_tags(self, venue_id: uuid.UUID, tag_ids: Sequence[uuid.UUID]) -> None:
        await self.session.execute(delete(VenueTag).where(VenueTag.venue_id == venue_id))
        self.session.add_all(VenueTag(venue_id=venue_id, tag_id=tag_id) for tag_id in tag_ids)
        await self.session.flush()

    async def replace_venue_items(self, venue_id: uuid.UUID, tag_ids: Sequence[uuid.UUID]) -> None:
        await self.session.execute(delete(VenueItem).where(VenueItem.venue_id == venue_id))
        self.session.add_all(
            VenueItem(venue_id=venue_id, tag_id=tag_id, position=position)
            for position, tag_id in enumerate(tag_ids)
        )
        await self.session.flush()

    async def add_visit_tag(self, visit_id: uuid.UUID, tag_id: uuid.UUID) -> bool:
        """True if this call saved it. A double tap or redelivered message saves one row."""
        saved = await self.session.scalar(
            insert(VisitTag)
            .values(visit_id=visit_id, tag_id=tag_id)
            .on_conflict_do_nothing()
            .returning(VisitTag.tag_id)
        )
        return saved is not None
