import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.common.dependencies import PaginationParams
from app.common.service import BaseService
from app.modules.venues.exceptions import (
    FoundingVenueNoTakenException,
    VenueLocationInvalidException,
    VenueNotFoundException,
    VenueScanCodeTakenException,
    VenueSlugTakenException,
)
from app.modules.venues.models import Venue, VenueStatus
from app.modules.venues.repository import VenueRepository
from app.modules.venues.scan_codes import scan_code_base, with_number
from app.modules.venues.schemas import VenueCreate, VenueUpdate
from app.modules.venues.slug import slugify, with_suffix

# After this many taken slugs something is wrong; stop instead of looping forever.
MAX_SLUG_ATTEMPTS = 50


class VenueService(BaseService[Venue]):
    not_found_message = VenueNotFoundException.message

    def __init__(self, repository: VenueRepository) -> None:
        super().__init__(repository)
        self.repository: VenueRepository = repository

    async def get_by_id(self, id_: uuid.UUID) -> Venue:
        venue = await self.repository.get(id_)
        if venue is None:
            raise VenueNotFoundException()
        return venue

    async def get_by_code(self, venue_code: str) -> Venue:
        venue = await self.repository.get_by_code(venue_code)
        if venue is None:
            raise VenueNotFoundException()
        return venue

    async def get_by_slug(self, city_id: uuid.UUID, slug: str) -> Venue:
        venue = await self.repository.get_by_slug(city_id, slug)
        if venue is None:
            raise VenueNotFoundException()
        return venue

    async def list_paginated(
        self,
        params: PaginationParams,
        *,
        city_id: uuid.UUID | None = None,
        area_id: uuid.UUID | None = None,
        category: str | None = None,
        status: str | None = None,
    ) -> tuple[Sequence[Venue], int]:
        filters: dict[str, Any] = {
            "city_id": city_id,
            "area_id": area_id,
            "category": category,
            "status": status,
        }
        items = await self.repository.list(offset=params.offset, limit=params.size, **filters)
        return items, await self.repository.count(**filters)

    async def create(self, payload: VenueCreate) -> Venue:  # type: ignore[override]
        await self._check_location(payload.city_id, payload.area_id)
        if payload.founding_venue_no is not None:
            await self._check_founding_venue_no(payload.founding_venue_no)

        data = payload.model_dump()
        if payload.slug is None:
            data["slug"] = await self._free_slug(payload.city_id, slugify(payload.name))
        elif await self.repository.slug_exists(payload.city_id, payload.slug):
            raise VenueSlugTakenException()
        if payload.scan_code is None:
            data["scan_code"] = await self._free_scan_code(scan_code_base(payload.name))
        elif await self.repository.scan_code_exists(payload.scan_code):
            raise VenueScanCodeTakenException()
        return await self.repository.create(data)

    async def update(self, id_: uuid.UUID, payload: VenueUpdate) -> Venue:  # type: ignore[override]
        venue = await self.get_by_id(id_)
        data = payload.model_dump(exclude_unset=True)

        # Renaming a venue keeps its slug, so links already shared keep working.
        if "slug" in data and await self.repository.slug_exists(
            venue.city_id, data["slug"], exclude_id=venue.id
        ):
            raise VenueSlugTakenException()
        # Changing the scan code makes the QRs on the counter phone change too (they are drawn
        # from it), so nothing printed goes stale.
        if "scan_code" in data and await self.repository.scan_code_exists(
            data["scan_code"], exclude_id=venue.id
        ):
            raise VenueScanCodeTakenException()
        if data.get("area_id") is not None:
            await self._check_location(venue.city_id, data["area_id"])
        if data.get("founding_venue_no") is not None:
            await self._check_founding_venue_no(data["founding_venue_no"], exclude_id=venue.id)
        return await self.repository.update(venue, data)

    async def set_status(
        self, id_: uuid.UUID, status: VenueStatus, *, admin_id: uuid.UUID | None = None
    ) -> Venue:
        venue = await self.get_by_id(id_)
        data: dict[str, Any] = {"status": status}
        # Remember who signed the venue the first time it is marked as signed.
        if status is VenueStatus.SIGNED and admin_id and venue.signed_by_admin_id is None:
            data["signed_by_admin_id"] = admin_id
        return await self.repository.update(venue, data)

    async def delete(self, id_: uuid.UUID) -> None:
        """Soft delete: the row stays for visits, points and billing history."""
        venue = await self.get_by_id(id_)
        await self.repository.update(venue, {"deleted_at": datetime.now(UTC)})

    async def _check_location(self, city_id: uuid.UUID, area_id: uuid.UUID | None) -> None:
        if await self.repository.get_city(city_id) is None:
            raise VenueLocationInvalidException()
        if area_id is None:
            return
        area = await self.repository.get_area(area_id)
        if area is None or area.city_id != city_id:
            raise VenueLocationInvalidException()

    async def _check_founding_venue_no(
        self, number: int, *, exclude_id: uuid.UUID | None = None
    ) -> None:
        if await self.repository.founding_venue_no_exists(number, exclude_id=exclude_id):
            raise FoundingVenueNoTakenException()

    async def _free_scan_code(self, base: str) -> str:
        """`KAHWA`, then `KAHWA2`, `KAHWA3`, ... until one is free."""
        candidate = base
        for number in range(2, MAX_SLUG_ATTEMPTS + 2):
            if not await self.repository.scan_code_exists(candidate):
                return candidate
            candidate = with_number(base, number)
        raise VenueScanCodeTakenException()

    async def _free_slug(self, city_id: uuid.UUID, base: str) -> str:
        """`kahwa-house`, then `kahwa-house-2`, `kahwa-house-3`, ... until one is free."""
        candidate = base
        for number in range(2, MAX_SLUG_ATTEMPTS + 2):
            if not await self.repository.slug_exists(city_id, candidate):
                return candidate
            candidate = with_suffix(base, number)
        raise VenueSlugTakenException()
