"""What a venue's owner sees and edits about their own place (team decision, 10 Nov 2026: owners
edit almost everything, but never the numbers). One login per place, so there is no team.

    editable     name, tagline (`description`), about, address, landmark, area (in its city),
                 opening hours, menu and website links, Instagram, contact number, amenities,
                 and whether it shows on Discover
    read-only    kind of place, scan code, status, founding number, billing; and every count,
                 which only ever comes from real Notchs

Renaming keeps the venue's link (slug) and scan code, so printed QRs and shared links keep working.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.modules.locations.models import Area, City
from app.modules.scans.niches import niche_of
from app.modules.venues.amenities import AMENITIES
from app.modules.venues.exceptions import VenueLocationInvalidException, VenueNotFoundException
from app.modules.venues.founding import (
    FOUNDING_DISCOUNT_PERCENT,
    FREE_MONTHS,
    founding_spots,
    founding_taken,
)
from app.modules.venues.models import Venue, VenueMember
from app.modules.venues.schemas import (
    AreaOption,
    VenueBillingOut,
    VenueProfileOut,
    VenueProfileUpdate,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Place:
    venue: Venue
    city: str
    area: Area | None
    areas: list[Area]


class VenueProfileService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def profile(self, member: VenueMember) -> VenueProfileOut:
        return self._out(await self._place(member))

    async def update(self, member: VenueMember, body: VenueProfileUpdate) -> VenueProfileOut:
        place = await self._place(member, lock=True)
        venue = place.venue
        data = body.model_dump(exclude_unset=True)
        if "area_id" in data and data["area_id"] is not None:
            area = await self.session.get(Area, data["area_id"])
            if area is None or area.city_id != venue.city_id:
                raise VenueLocationInvalidException()
        for field, value in data.items():
            setattr(venue, field, value)
        await self.session.flush()
        logger.info("Venue %s updated its profile: %s", venue.venue_code, sorted(data))
        return self._out(await self._place(member))

    async def billing(self, member: VenueMember) -> VenueBillingOut:
        venue = (await self._place(member)).venue
        spots = founding_spots(venue.category)
        return VenueBillingOut(
            amount_due_paise=0,
            free_months=FREE_MONTHS,
            founding_no=venue.founding_venue_no,
            founding_spots=spots,
            founding_spots_left=max(0, spots - await founding_taken(self.session, venue.category)),
            founding_discount_percent=FOUNDING_DISCOUNT_PERCENT,
            niche=niche_of(venue.category).label,
        )

    async def _place(self, member: VenueMember, *, lock: bool = False) -> _Place:
        query = (
            select(Venue, City.name, Area)
            .join(City, City.id == Venue.city_id)
            .outerjoin(Area, Area.id == Venue.area_id)
            .where(Venue.id == member.venue_id, Venue.deleted_at.is_(None))
        )
        if lock:
            query = query.with_for_update(of=Venue)
        row = (await self.session.execute(query)).first()
        if row is None:
            raise VenueNotFoundException()
        venue, city, area = row
        areas = await self.session.scalars(
            select(Area).where(Area.city_id == venue.city_id).order_by(Area.name)
        )
        return _Place(venue, city, area, list(areas))

    @staticmethod
    def _out(place: _Place) -> VenueProfileOut:
        venue = place.venue
        return VenueProfileOut(
            venue_code=venue.venue_code,
            name=venue.name,
            category=venue.category,
            niche=niche_of(venue.category).label,
            scan_code=venue.scan_code,
            status=venue.status,
            city=place.city,
            area_id=place.area.id if place.area else None,
            area=place.area.name if place.area else None,
            address=venue.address,
            landmark=venue.landmark,
            description=venue.description,
            about=venue.about,
            menu_url=venue.menu_url,
            website_url=venue.website_url,
            instagram_handle=venue.instagram_handle,
            contact_phone=venue.contact_phone,
            amenities=venue.amenities or [],
            visible_on_discover=venue.visible_on_discover,
            opening_hours=venue.opening_hours,
            area_options=[AreaOption(id=a.id, name=a.name) for a in place.areas],
            amenity_options=list(AMENITIES),
        )
