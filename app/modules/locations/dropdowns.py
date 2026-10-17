"""Dropdown lists for cities and areas (see app/modules/dropdowns/registry.py)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dropdowns.registry import Dropdown, Filters, register, uuid_filter
from app.modules.dropdowns.schemas import DropdownOption
from app.modules.locations.models import Area, City


async def load_cities(session: AsyncSession, _: Filters) -> list[DropdownOption]:
    """Cities that are live for users and venues."""
    rows = await session.execute(
        select(City.id, City.name).where(City.is_active.is_(True)).order_by(City.name)
    )
    return [DropdownOption(value=str(id_), label=name) for id_, name in rows]


async def load_areas(session: AsyncSession, filters: Filters) -> list[DropdownOption]:
    city_id = uuid_filter(filters, "city_id")
    rows = await session.execute(
        select(Area.id, Area.name).where(Area.city_id == city_id).order_by(Area.name)
    )
    return [DropdownOption(value=str(id_), label=name) for id_, name in rows]


register(Dropdown("cities", load_cities))
register(Dropdown("areas", load_areas, required_filters=("city_id",)))
