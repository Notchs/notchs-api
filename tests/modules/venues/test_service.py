"""Business rules of `VenueService`, against an in-memory repository (no database)."""

import uuid
from typing import Any

import pytest

from app.common.dependencies import PaginationParams
from app.modules.locations.models import Area, City
from app.modules.venues.exceptions import (
    FoundingVenueNoTakenException,
    VenueLocationInvalidException,
    VenueNotFoundException,
    VenueScanCodeTakenException,
    VenueSlugTakenException,
)
from app.modules.venues.models import Venue, VenueStatus
from app.modules.venues.schemas import VenueCreate, VenueUpdate
from app.modules.venues.service import VenueService


class FakeVenueRepository:
    def __init__(self) -> None:
        self.venues: dict[uuid.UUID, Venue] = {}
        self.cities: dict[uuid.UUID, City] = {}
        self.areas: dict[uuid.UUID, Area] = {}

    def add_city(self) -> City:
        city = City(id=uuid.uuid4(), name="Ahmedabad", slug=f"city-{len(self.cities)}")
        self.cities[city.id] = city
        return city

    def add_area(self, city: City) -> Area:
        area = Area(id=uuid.uuid4(), city_id=city.id, name="SBR", slug=f"area-{len(self.areas)}")
        self.areas[area.id] = area
        return area

    def _live(self) -> list[Venue]:
        return [venue for venue in self.venues.values() if venue.deleted_at is None]

    async def get(self, id_: uuid.UUID) -> Venue | None:
        return next((v for v in self._live() if v.id == id_), None)

    async def get_by_code(self, venue_code: str) -> Venue | None:
        return next((v for v in self._live() if v.venue_code == venue_code.upper()), None)

    async def get_by_slug(self, city_id: uuid.UUID, slug: str) -> Venue | None:
        return next((v for v in self._live() if (v.city_id, v.slug) == (city_id, slug)), None)

    async def list(self, *, offset: int = 0, limit: int = 20, **filters: Any) -> list[Venue]:
        return self._filtered(filters)[offset : offset + limit]

    async def count(self, **filters: Any) -> int:
        return len(self._filtered(filters))

    def _filtered(self, filters: dict[str, Any]) -> list[Venue]:
        wanted = {key: value for key, value in filters.items() if value is not None}
        return [v for v in self._live() if all(getattr(v, k) == w for k, w in wanted.items())]

    async def slug_exists(
        self, city_id: uuid.UUID, slug: str, *, exclude_id: uuid.UUID | None = None
    ) -> bool:
        return any(
            (v.city_id, v.slug) == (city_id, slug) and v.id != exclude_id
            for v in self.venues.values()
        )

    async def scan_code_exists(
        self, scan_code: str, *, exclude_id: uuid.UUID | None = None
    ) -> bool:
        return any(v.scan_code == scan_code and v.id != exclude_id for v in self.venues.values())

    async def founding_venue_no_exists(
        self, number: int, *, exclude_id: uuid.UUID | None = None
    ) -> bool:
        return any(
            v.founding_venue_no == number and v.id != exclude_id for v in self.venues.values()
        )

    async def get_city(self, city_id: uuid.UUID) -> City | None:
        return self.cities.get(city_id)

    async def get_area(self, area_id: uuid.UUID) -> Area | None:
        return self.areas.get(area_id)

    async def create(self, data: dict[str, Any]) -> Venue:
        venue = Venue(id=uuid.uuid4(), status=VenueStatus.PENDING, **data)
        venue.venue_code = f"VEN-{len(self.venues):08d}"
        self.venues[venue.id] = venue
        return venue

    async def update(self, obj: Venue, data: dict[str, Any]) -> Venue:
        for field, value in data.items():
            setattr(obj, field, value)
        return obj


@pytest.fixture
def repo() -> FakeVenueRepository:
    return FakeVenueRepository()


@pytest.fixture
def service(repo: FakeVenueRepository) -> VenueService:
    return VenueService(repo)  # type: ignore[arg-type]


@pytest.fixture
def city(repo: FakeVenueRepository) -> City:
    return repo.add_city()


def _payload(city: City, **overrides: Any) -> VenueCreate:
    data: dict[str, Any] = {"city_id": city.id, "name": "Kahwa House", "category": "cafe"}
    return VenueCreate(**(data | overrides))


async def test_create_generates_the_slug_from_the_name(service: VenueService, city: City) -> None:
    venue = await service.create(_payload(city))

    assert venue.slug == "kahwa-house"
    assert venue.status == VenueStatus.PENDING


async def test_create_numbers_the_slug_when_the_name_repeats(
    service: VenueService, city: City
) -> None:
    slugs = [(await service.create(_payload(city))).slug for _ in range(3)]

    assert slugs == ["kahwa-house", "kahwa-house-2", "kahwa-house-3"]


async def test_same_slug_is_fine_in_another_city(
    service: VenueService, repo: FakeVenueRepository, city: City
) -> None:
    await service.create(_payload(city))
    other = await service.create(_payload(repo.add_city()))

    assert other.slug == "kahwa-house"


async def test_a_deleted_venue_keeps_its_slug_reserved(service: VenueService, city: City) -> None:
    first = await service.create(_payload(city))
    await service.delete(first.id)

    assert (await service.create(_payload(city))).slug == "kahwa-house-2"


async def test_create_rejects_an_explicit_slug_that_is_taken(
    service: VenueService, city: City
) -> None:
    await service.create(_payload(city))

    with pytest.raises(VenueSlugTakenException):
        await service.create(_payload(city, name="Another", slug="kahwa-house"))


async def test_create_rejects_unknown_city(service: VenueService, city: City) -> None:
    with pytest.raises(VenueLocationInvalidException):
        await service.create(_payload(city, city_id=uuid.uuid4()))


async def test_create_rejects_area_from_another_city(
    service: VenueService, repo: FakeVenueRepository, city: City
) -> None:
    foreign_area = repo.add_area(repo.add_city())

    with pytest.raises(VenueLocationInvalidException):
        await service.create(_payload(city, area_id=foreign_area.id))


async def test_create_accepts_area_in_the_same_city(
    service: VenueService, repo: FakeVenueRepository, city: City
) -> None:
    area = repo.add_area(city)

    assert (await service.create(_payload(city, area_id=area.id))).area_id == area.id


async def test_founding_venue_no_is_unique(service: VenueService, city: City) -> None:
    await service.create(_payload(city, founding_venue_no=1))

    with pytest.raises(FoundingVenueNoTakenException):
        await service.create(_payload(city, name="Second", founding_venue_no=1))


async def test_rename_keeps_the_slug(service: VenueService, city: City) -> None:
    venue = await service.create(_payload(city))

    updated = await service.update(venue.id, VenueUpdate(name="Kahwa House & Kitchen"))

    assert (updated.name, updated.slug) == ("Kahwa House & Kitchen", "kahwa-house")


async def test_update_rejects_a_slug_another_venue_uses(service: VenueService, city: City) -> None:
    await service.create(_payload(city))
    second = await service.create(_payload(city, name="Second"))

    with pytest.raises(VenueSlugTakenException):
        await service.update(second.id, VenueUpdate(slug="kahwa-house"))


async def test_update_may_resend_the_venues_own_slug_and_number(
    service: VenueService, city: City
) -> None:
    venue = await service.create(_payload(city, founding_venue_no=7))

    updated = await service.update(
        venue.id, VenueUpdate(slug="kahwa-house", founding_venue_no=7, description="Hi")
    )

    assert updated.description == "Hi"


async def test_update_rejects_area_from_another_city(
    service: VenueService, repo: FakeVenueRepository, city: City
) -> None:
    venue = await service.create(_payload(city))
    foreign_area = repo.add_area(repo.add_city())

    with pytest.raises(VenueLocationInvalidException):
        await service.update(venue.id, VenueUpdate(area_id=foreign_area.id))


async def test_signing_records_the_admin_once(service: VenueService, city: City) -> None:
    venue = await service.create(_payload(city))
    first_admin, second_admin = uuid.uuid4(), uuid.uuid4()

    await service.set_status(venue.id, VenueStatus.SIGNED, admin_id=first_admin)
    await service.set_status(venue.id, VenueStatus.PAUSED, admin_id=second_admin)
    signed_again = await service.set_status(venue.id, VenueStatus.SIGNED, admin_id=second_admin)

    assert signed_again.status == VenueStatus.SIGNED
    assert signed_again.signed_by_admin_id == first_admin


async def test_deleted_venues_are_hidden(service: VenueService, city: City) -> None:
    venue = await service.create(_payload(city))
    await service.delete(venue.id)

    with pytest.raises(VenueNotFoundException):
        await service.get_by_id(venue.id)
    with pytest.raises(VenueNotFoundException):
        await service.get_by_slug(city.id, "kahwa-house")
    with pytest.raises(VenueNotFoundException):
        await service.get_by_code(venue.venue_code)


async def test_lookup_by_code_ignores_case(service: VenueService, city: City) -> None:
    venue = await service.create(_payload(city))

    assert (await service.get_by_code(venue.venue_code.lower())).id == venue.id


async def test_list_filters_and_counts(service: VenueService, city: City) -> None:
    await service.create(_payload(city))
    bar = await service.create(_payload(city, name="Night Bar", category="bar"))
    gone = await service.create(_payload(city, name="Gone", category="bar"))
    await service.delete(gone.id)

    items, total = await service.list_paginated(PaginationParams(page=1, size=10), category="bar")

    assert ([venue.id for venue in items], total) == ([bar.id], 1)


# --- scan codes ---------------------------------------------------------------------------------


async def test_create_makes_the_scan_code_from_the_name(service: VenueService, city: City) -> None:
    codes = [(await service.create(_payload(city))).scan_code for _ in range(3)]
    other_city = await service.create(_payload(city, name="Kahwa Corner"))

    assert codes == ["KAHWA", "KAHWA2", "KAHWA3"]
    assert other_city.scan_code == "KAHWA4"  # unique everywhere, not per city


async def test_create_keeps_a_given_scan_code(service: VenueService, city: City) -> None:
    venue = await service.create(_payload(city, scan_code="kaafi"))

    assert venue.scan_code == "KAAFI"
    with pytest.raises(VenueScanCodeTakenException):
        await service.create(_payload(city, name="Other", scan_code="KAAFI"))


async def test_update_scan_code(service: VenueService, city: City) -> None:
    first = await service.create(_payload(city))
    second = await service.create(_payload(city, name="Second"))

    renamed = await service.update(second.id, VenueUpdate(scan_code="SECOND"))
    same = await service.update(first.id, VenueUpdate(scan_code="KAHWA"))  # its own code

    assert (renamed.scan_code, same.scan_code) == ("SECOND", "KAHWA")
    with pytest.raises(VenueScanCodeTakenException):
        await service.update(second.id, VenueUpdate(scan_code="kahwa"))
