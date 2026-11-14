"""Demo cafés for pitches (app/db/demo.py): 20 made-up Rajkot cafés, never on the shared
database, and removable without touching anything else."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.demo import CAFES, DemoNotAllowed, check_allowed, remove_demo, seed_demo
from app.db.seed import seed
from app.modules.locations.models import Area, City, CityStatus
from app.modules.scans.models import Visit
from app.modules.users.models import User
from app.modules.venues.models import Venue, VenueStatus

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_rajkot_is_a_live_city_with_its_areas(db: AsyncSession) -> None:
    await seed(db)
    rajkot = await db.scalar(select(City).where(City.slug == "rajkot"))
    areas = await db.scalar(select(func.count()).select_from(Area).where(Area.city_id == rajkot.id))
    assert rajkot.status == CityStatus.LIVE and rajkot.state == "Gujarat" and areas == 12


async def test_adds_twenty_cafes_once_and_nothing_else(db: AsyncSession) -> None:
    await seed(db)
    users_before = await db.scalar(select(func.count()).select_from(User))

    assert await seed_demo(db) == len(CAFES) == 20
    assert await seed_demo(db) == 0  # already there

    rajkot = await db.scalar(select(City).where(City.slug == "rajkot"))
    venues = list(await db.scalars(select(Venue).where(Venue.scan_code.like("DM%"))))
    assert len(venues) == 20 and len({v.slug for v in venues}) == 20
    assert all(v.city_id == rajkot.id and v.area_id is not None for v in venues)
    assert all(v.price_range in (1, 2) and v.status == VenueStatus.COUNTER_LIVE for v in venues)
    # Cafés only: no customers, visits or logins made up.
    assert await db.scalar(select(func.count()).select_from(User)) == users_before
    assert await db.scalar(select(func.count()).select_from(Visit)) == 0


async def test_remove_takes_out_only_the_demo_cafes(db: AsyncSession) -> None:
    await seed(db)
    rajkot = await db.scalar(select(City).where(City.slug == "rajkot"))
    real = Venue(city_id=rajkot.id, name="Real Place", slug="real-place", category="cafe")
    db.add(real)
    await seed_demo(db)

    assert await remove_demo(db) == 20
    assert (
        await db.scalar(select(func.count()).select_from(Venue).where(Venue.scan_code.like("DM%")))
        == 0
    )
    assert await db.get(Venue, real.id) is not None
    assert await remove_demo(db) == 0


LOCAL = "postgresql+asyncpg://postgres@127.0.0.1:5432/notchs"
NEON = "postgresql+asyncpg://u@ep-x.neon.tech/neondb"


@pytest.mark.parametrize(
    ("url", "schema", "environment", "allowed"),
    [
        (LOCAL, "public", "local", True),
        ("postgresql+asyncpg://postgres@db:5432/notchs", "public", "local", True),
        (NEON, "dev_harsh", "local", True),
        (NEON, "public", "local", False),  # the shared database behind the live site
        (NEON, "test_x", "local", False),
        (LOCAL, "public", "staging", False),
        (NEON, "dev_harsh", "production", False),
    ],
)
def test_never_on_the_shared_database_unless_asked(
    url: str, schema: str, environment: str, allowed: bool
) -> None:
    settings = Settings.model_construct(DATABASE_URL=url, DB_SCHEMA=schema, ENVIRONMENT=environment)
    if allowed:
        check_allowed(settings)
    else:
        with pytest.raises(DemoNotAllowed):
            check_allowed(settings)


def test_shared_only_by_name_and_never_from_a_server() -> None:
    shared = Settings.model_construct(DATABASE_URL=NEON, DB_SCHEMA="public", ENVIRONMENT="local")
    check_allowed(shared, shared=True)
    for environment in ("staging", "production"):
        server = Settings.model_construct(
            DATABASE_URL=NEON, DB_SCHEMA="public", ENVIRONMENT=environment
        )
        with pytest.raises(DemoNotAllowed):
            check_allowed(server, shared=True)
