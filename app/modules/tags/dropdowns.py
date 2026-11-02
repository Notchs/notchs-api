"""The tags dropdown (see app/modules/dropdowns/registry.py): `/dropdowns/tags?kind=item`."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dropdowns.exceptions import DropdownFilterInvalidException
from app.modules.dropdowns.registry import Dropdown, Filters, register
from app.modules.dropdowns.schemas import DropdownOption
from app.modules.tags.models import Tag, TagKind


async def load_tags(session: AsyncSession, filters: Filters) -> list[DropdownOption]:
    query = select(Tag.id, Tag.name).order_by(Tag.name)
    kind = filters.get("kind")
    if kind:
        if kind not in TagKind:
            raise DropdownFilterInvalidException("kind must be a valid tag kind.")
        query = query.where(Tag.kind == kind)
    rows = await session.execute(query)
    return [DropdownOption(value=str(id_), label=name) for id_, name in rows]


register(Dropdown("tags", load_tags))
