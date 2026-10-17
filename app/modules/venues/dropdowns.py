"""Dropdown lists owned by the venues module (see app/modules/dropdowns/registry.py)."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dropdowns.registry import Dropdown, Filters, register
from app.modules.dropdowns.schemas import DropdownOption
from app.modules.venues.models import VenueCategory

CATEGORY_LABELS = {
    VenueCategory.CAFE: "Cafe",
    VenueCategory.RESTAURANT: "Restaurant",
    VenueCategory.BAR: "Bar",
    VenueCategory.HOTEL: "Hotel",
    VenueCategory.TURF_GYM: "Turf / Gym",
    VenueCategory.OTHER: "Other",
}


async def load_categories(_: AsyncSession, __: Filters) -> list[DropdownOption]:
    return [
        DropdownOption(value=category.value, label=CATEGORY_LABELS[category])
        for category in VenueCategory
    ]


register(Dropdown("venue_categories", load_categories))
