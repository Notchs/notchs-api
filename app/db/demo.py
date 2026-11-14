"""Demo cafés for pitching to venue owners: 30 made-up cafés in Rajkot, no customers or visits.

    python -m app.cli seed-demo            # add the ones not there yet
    python -m app.cli seed-demo --remove   # take them out again
    python -m app.cli seed-demo --shared   # on the shared database (team decision, 14 Nov 2026)

The cafés are invented, never real places, so no real business is listed as if it had joined,
and each says so in its `about` text. Without `--shared` it runs only on a local database or a
`dev_<name>` schema. CLAUDE.md §4 keeps made-up data out of `public`; the team agreed to these
demo cafés there on 14 Nov 2026, so `--shared` must be asked for by name.
"""

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

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
IST = ZoneInfo("Asia/Kolkata")
# Demo cafés "joined" between 9 am and 9 pm on different days of last month.
OPEN_HOUR, CLOSE_HOUR = 9, 21


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
    DemoCafe("Masala Mug", "mavdi", 1, "Masala chai in big mugs, evening crowd."),
    DemoCafe("Bun Maska Bureau", "university-road", 1, "Bun maska, cutting chai and exam talk."),
    DemoCafe("Green Door Café", "nana-mava", 2, "Plants, pour-overs and slow afternoons."),
    DemoCafe("Cutting & Co.", "kotecha-chowk", 1, "Cutting chai and quick bites."),
    DemoCafe("Rangilo Roast", "gondal-road", 2, "Bright cups and Gujarati snacks."),
    DemoCafe("Half Moon Café", "indira-circle", 2, "Cold coffee and late-night fries."),
    DemoCafe("Lime Tree Café", "race-course", 2, "Lemon iced tea under the trees."),
    DemoCafe("Kadak Corner", "raiya-road", 1, "Strong chai, open early."),
    DemoCafe("Sunday Sip", "amin-marg", 2, "Brunch plates and frappés."),
    DemoCafe("Station Brew", "yagnik-road", 1, "Quick coffee on the way to work."),
)


class DemoNotAllowed(Exception):
    """The database is the shared one behind the live site, or the app runs as staging/prod."""


def is_shared(settings: Settings) -> bool:
    """True for a non-local database outside a `dev_<name>` schema: the one behind the site."""
    host = make_url(settings.DATABASE_URL).host or ""
    return not settings.DB_SCHEMA.startswith("dev_") and host not in LOCAL_HOSTS


def check_allowed(settings: Settings, *, shared: bool = False) -> None:
    """Never from a deployed server. On the shared database only when `shared` is asked for
    (CLAUDE.md §4; the team agreed to the demo cafés there on 14 Nov 2026)."""
    if settings.ENVIRONMENT in ("staging", "production"):
        raise DemoNotAllowed(f"ENVIRONMENT is {settings.ENVIRONMENT}: run it from your machine.")
    if is_shared(settings) and not shared:
        raise DemoNotAllowed(
            f"DB_SCHEMA is {settings.DB_SCHEMA} on a shared database. "
            "Add --shared only if the team agreed to demo cafés there."
        )


def slugify(name: str) -> str:
    """'Oak & Elaichi' -> 'oak-and-elaichi', 'Third Cup Café' -> 'third-cup-cafe'."""
    text = name.lower().replace("&", "and").replace("é", "e")
    return "-".join("".join(c if c.isalnum() else " " for c in text).split())


def scan_code(number: int, cafe: DemoCafe) -> str:
    """'DM01BREW': the DM prefix marks demo cafés; the number keeps every code unique."""
    return f"{SCAN_CODE_PREFIX}{number:02d}{slugify(cafe.name).replace('-', '')[:4].upper()}"


def onboarding_times(now: datetime) -> list[datetime]:
    """One different date and time last month (in India) per demo café, in CAFES order, so
    nothing looks like it joined this month. Same answer for the same month."""
    this_month = now.astimezone(IST).date().replace(day=1)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    days = (this_month - last_month).days
    minutes_a_day = (CLOSE_HOUR - OPEN_HOUR) * 60
    slots = random.Random(last_month.toordinal()).sample(range(days * minutes_a_day), len(CAFES))
    times = []
    for slot in slots:
        day, minute = divmod(slot, minutes_a_day)
        local = datetime.combine(last_month + timedelta(days=day), time(OPEN_HOUR), tzinfo=IST)
        times.append((local + timedelta(minutes=minute)).astimezone(UTC))
    return times


async def _demo_venue_ids(session: AsyncSession) -> list[uuid.UUID]:
    rows = await session.scalars(
        select(Venue.id).where(Venue.scan_code.like(f"{SCAN_CODE_PREFIX}%"))
    )
    return list(rows)


async def seed_demo(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Add the demo cafés that are not there yet, each "joined" last month (onboarding_times).
    Returns how many were added."""
    city = await session.scalar(select(City).where(City.slug == CITY_SLUG))
    if city is None:
        raise LookupError("Rajkot is missing: run `python -m app.cli seed` first.")
    existing = set(
        await session.scalars(
            select(Venue.scan_code).where(Venue.scan_code.like(f"{SCAN_CODE_PREFIX}%"))
        )
    )
    areas = {a.slug: a for a in await session.scalars(select(Area).where(Area.city_id == city.id))}
    joined = onboarding_times(now or datetime.now(UTC))
    added = 0
    for number, cafe in enumerate(CAFES, start=1):
        code = scan_code(number, cafe)
        if code in existing:
            continue
        area = areas[cafe.area_slug]
        at = joined[number - 1]
        session.add(
            Venue(
                city_id=city.id,
                area_id=area.id,
                name=cafe.name,
                slug=slugify(cafe.name),
                category=VenueCategory.CAFE,
                scan_code=code,
                address=f"{area.name}, Rajkot",
                description=cafe.description,
                about=DEMO_NOTE,
                price_range=cafe.price_range,
                status=VenueStatus.COUNTER_LIVE,
                signed_at=at,
                live_at=at,
                created_at=at,
                updated_at=at,
            )
        )
        added += 1
    await session.flush()
    return added


async def redate_demo(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Move the demo cafés already there to their dates last month. Returns how many moved."""
    joined = onboarding_times(now or datetime.now(UTC))
    moved = 0
    for number, cafe in enumerate(CAFES, start=1):
        venue = await session.scalar(
            select(Venue).where(Venue.scan_code == scan_code(number, cafe))
        )
        if venue is None:
            continue
        at = joined[number - 1]
        venue.signed_at = venue.live_at = venue.created_at = at
        moved += 1
    await session.flush()
    return moved


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
