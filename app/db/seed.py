"""Seed data: safe to run any number of times. Upserts by natural key and never
deletes, so data other people added is left alone.

    python -m app.cli seed
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ids import new_uuid
from app.modules.locations.models import Area, City, CityStatus


@dataclass(frozen=True, slots=True)
class SeedCity:
    name: str
    slug: str
    state: str
    areas: tuple[tuple[str, str], ...]  # (name, slug)


CITIES = (
    SeedCity(
        name="Ahmedabad",
        slug="ahmedabad",
        state="Gujarat",
        areas=(
            ("Bodakdev", "bodakdev"),
            ("C G Road", "cg-road"),
            ("Navrangpura", "navrangpura"),
            ("Prahlad Nagar", "prahlad-nagar"),
            ("Satellite", "satellite"),
            ("SBR (Sindhu Bhavan Road)", "sbr"),
            ("Thaltej", "thaltej"),
            ("Vastrapur", "vastrapur"),
        ),
    ),
)


async def seed(session: AsyncSession) -> dict[str, int]:
    """Make the seed cities active and add their areas. Returns how many rows each step touched."""
    counts = {"cities": 0, "areas": 0}
    for city in CITIES:
        await session.execute(
            insert(City)
            .values(
                id=new_uuid(),
                name=city.name,
                slug=city.slug,
                state=city.state,
                status=CityStatus.LIVE,
            )
            .on_conflict_do_update(
                index_elements=[City.slug],
                set_={"name": city.name, "state": city.state, "status": CityStatus.LIVE},
            )
        )
        counts["cities"] += 1
        city_id = await session.scalar(select(City.id).where(City.slug == city.slug))
        for name, slug in city.areas:
            await session.execute(
                insert(Area)
                .values(id=new_uuid(), city_id=city_id, name=name, slug=slug)
                .on_conflict_do_update(constraint="uq_areas_city_id_slug", set_={"name": name})
            )
            counts["areas"] += 1
    return counts
