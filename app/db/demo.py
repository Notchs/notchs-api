"""Demo cafés for pitching to venue owners: 20 made-up cafés in Rajkot, no customers or visits.

    python -m app.cli seed-demo            # add them (does nothing if they are already there)
    python -m app.cli seed-demo --remove   # take them out again

The cafés are invented, never real places, so no real business is listed as if it had joined.
Runs only on a local database or a `dev_<name>` schema, never on the shared `public` schema
behind the live site (CLAUDE.md §4: real data only in `public`).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, make_url, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db import models  # noqa: F401  (every table, so --remove finds all links to venues)
from app.db.base import Base
from app.modules.locations.models import Area, City
from app.modules.venues.models import Venue, VenueCategory, VenueStatus

CITY_SLUG = "rajkot"
# Demo cafés are found again by this, so --remove never touches anything else.
SCAN_CODE_PREFIX = "DM"
DEMO_NOTE = "Demo café for showing Notchs. Not a real place."
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "db", "postgres"}


@dataclass(frozen=True, slots=True)
class DemoCafe:
    name: str
    area_slug: str
    price_range: int  # 1 or 2: everyday, low to medium
    description: str


CAFES = (
    DemoCafe("Brew Ghar", "kalawad-road", 2, "Filter coffee and cold brew by the window."),
    DemoCafe("Third Cup Café", "university-road", 1, "Student favourite, open late."),
    DemoCafe("Ringroad Roastery", "150-feet-ring-road", 2, "Small-batch roasts."),
    DemoCafe("Chai Ni Talab", "yagnik-road", 1, "Kadak chai, bun maska and friends."),
    DemoCafe("Little Kesar Café", "race-course", 2, "Saffron latte and evening walks."),
    DemoCafe("The Rainy Window", "amin-marg", 2, "Board games and hot chocolate."),
    DemoCafe("Paper Straw Café", "raiya-road", 1, "Shakes, fries and loud music."),
    DemoCafe("Kalawad Coffee Room", "kalawad-road", 2, "A quiet room for working."),
    DemoCafe("Doodh Patti Lane", "mavdi", 1, "Doodh patti and Maggi till midnight."),
    DemoCafe("Gully Grind", "nana-mava", 1, "Street-style coffee, rooftop seats."),
    DemoCafe("Saanj Café", "kotecha-chowk", 2, "Sunset seating and sandwiches."),
    DemoCafe("Nukkad Brew Bar", "indira-circle", 1, "Iced coffee on the corner."),
    DemoCafe("Kesudo Café", "gondal-road", 1, "Homely snacks and masala chai."),
    DemoCafe("Moonlight Maska", "university-road", 1, "Late-night maska bun and cutting."),
    DemoCafe("Patang Café", "race-course", 2, "Colourful corner for weekend brunch."),
    DemoCafe("The Hungry Sparrow", "150-feet-ring-road", 2, "Waffles, pasta and coffee."),
    DemoCafe("Raiya Roast Corner", "raiya-road", 1, "Cold coffee in big glasses."),
    DemoCafe("Oak & Elaichi", "amin-marg", 2, "Cardamom cappuccino and cake."),
    DemoCafe("Second Shift Café", "kalawad-road", 1, "Opens after college, closes late."),
    DemoCafe("Tapri Studio", "yagnik-road", 1, "Chai, music nights and open mics."),
)


class DemoNotAllowed(Exception):
    """The database is the shared one behind the live site, or the app runs as staging/prod."""


def check_allowed(settings: Settings) -> None:
    """Demo data only on a local database or a `dev_<name>` schema (CLAUDE.md §4)."""
    if settings.ENVIRONMENT in ("staging", "production"):
        raise DemoNotAllowed(f"ENVIRONMENT is {settings.ENVIRONMENT}: demo data is local only.")
    host = make_url(settings.DATABASE_URL).host or ""
    if not settings.DB_SCHEMA.startswith("dev_") and host not in LOCAL_HOSTS:
        raise DemoNotAllowed(
            f"DB_SCHEMA is {settings.DB_SCHEMA} on a shared database. Use a dev_<name> schema."
        )


def slugify(name: str) -> str:
    """'Oak & Elaichi' -> 'oak-and-elaichi', 'Third Cup Café' -> 'third-cup-cafe'."""
    text = name.lower().replace("&", "and").replace("é", "e")
    return "-".join("".join(c if c.isalnum() else " " for c in text).split())


async def _demo_venue_ids(session: AsyncSession) -> list[uuid.UUID]:
    rows = await session.scalars(
        select(Venue.id).where(Venue.scan_code.like(f"{SCAN_CODE_PREFIX}%"))
    )
    return list(rows)


async def seed_demo(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Add the demo cafés. Returns how many were added (0 when they are already there)."""
    if await _demo_venue_ids(session):
        return 0
    city = await session.scalar(select(City).where(City.slug == CITY_SLUG))
    if city is None:
        raise LookupError("Rajkot is missing: run `python -m app.cli seed` first.")
    areas = {a.slug: a for a in await session.scalars(select(Area).where(Area.city_id == city.id))}
    now = now or datetime.now(UTC)
    for i, cafe in enumerate(CAFES, start=1):
        area = areas[cafe.area_slug]
        slug = slugify(cafe.name)
        session.add(
            Venue(
                city_id=city.id,
                area_id=area.id,
                name=cafe.name,
                slug=slug,
                category=VenueCategory.CAFE,
                scan_code=f"{SCAN_CODE_PREFIX}{i:02d}{slug.replace('-', '')[:4].upper()}",
                address=f"{area.name}, Rajkot",
                description=cafe.description,
                about=DEMO_NOTE,
                price_range=cafe.price_range,
                status=VenueStatus.COUNTER_LIVE,
                signed_at=now,
                live_at=now,
            )
        )
    await session.flush()
    return len(CAFES)


async def remove_demo(session: AsyncSession) -> int:
    """Delete the demo cafés and anything attached to them. Returns how many were removed."""
    venue_ids = await _demo_venue_ids(session)
    if not venue_ids:
        return 0
    # Anything added to a demo café while showing it (a paired counter, a test scan...),
    # children first.
    for table in reversed(Base.metadata.sorted_tables):
        for fk in table.foreign_keys:
            if fk.column.table.name == "venues" and table.name != "venues":
                await session.execute(delete(table).where(fk.parent.in_(venue_ids)))
    await session.execute(delete(Venue).where(Venue.id.in_(venue_ids)))
    return len(venue_ids)
