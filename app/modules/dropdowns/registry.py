"""The dropdown registry: every list served by `GET /dropdowns/{name}`.

A module adds a list by calling `register()` in its own `dropdowns.py`, and that file is
imported in `app/modules/dropdowns/lists.py`. No new endpoint is needed.

    register(Dropdown("venue_categories", load_categories))
    register(Dropdown("areas", load_areas, required_filters=("city_id",)))
"""

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dropdowns.exceptions import (
    DropdownFilterInvalidException,
    DropdownNotFoundException,
)
from app.modules.dropdowns.schemas import DropdownOption

Filters = Mapping[str, str]
Loader = Callable[[AsyncSession, Filters], Awaitable[list[DropdownOption]]]


@dataclass(frozen=True, slots=True)
class Dropdown:
    name: str
    load: Loader
    # Query parameters the list cannot be built without, e.g. `city_id` for areas.
    required_filters: tuple[str, ...] = ()


_REGISTRY: dict[str, Dropdown] = {}


def register(dropdown: Dropdown) -> Dropdown:
    if dropdown.name in _REGISTRY:
        raise ValueError(f"Dropdown {dropdown.name!r} is registered twice")
    _REGISTRY[dropdown.name] = dropdown
    return dropdown


def names() -> list[str]:
    return sorted(_REGISTRY)


async def load(session: AsyncSession, name: str, filters: Filters) -> list[DropdownOption]:
    dropdown = _REGISTRY.get(name)
    if dropdown is None:
        raise DropdownNotFoundException(details={"available": names()})
    missing = [f for f in dropdown.required_filters if not filters.get(f)]
    if missing:
        raise DropdownFilterInvalidException(
            f"The {name!r} dropdown needs: {', '.join(missing)}.", details={"missing": missing}
        )
    return await dropdown.load(session, filters)


def uuid_filter(filters: Filters, key: str) -> uuid.UUID:
    """Read a UUID query parameter, or raise the dropdown's 422."""
    try:
        return uuid.UUID(filters[key])
    except (KeyError, ValueError) as exc:
        raise DropdownFilterInvalidException(f"{key} must be a valid id.") from exc
