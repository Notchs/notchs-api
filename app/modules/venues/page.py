"""The venue page: what anyone sees (A8), and the shared pieces of a venue used in lists
(places, rankings, cards). The passport adds the customer's own layer on top (C6).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.locations.models import Area, City
from app.modules.locations.time import local, month_of
from app.modules.notches import badges
from app.modules.rankings import service as rankings
from app.modules.scans.models import COUNTED_STATUSES, Visit
from app.modules.users.display import public_name
from app.modules.users.models import User
from app.modules.venues.hours import OpenStatus, status_at
from app.modules.venues.models import Venue
from app.modules.venues.schemas import (
    OpenStatusOut,
    RegularNowOut,
    VenueBriefOut,
    VenuePageOut,
)


def photo_url(key: str | None) -> str | None:
    if not key or not settings.MEDIA_BASE_URL:
        return None
    return f"{settings.MEDIA_BASE_URL.rstrip('/')}/{quote(key)}"


def directions_url(venue: Venue) -> str | None:
    """Google Maps directions ("Get directions", A8's main action)."""
    if venue.latitude is not None and venue.longitude is not None:
        destination = f"{venue.latitude},{venue.longitude}"
    elif venue.address:
        destination = f"{venue.name}, {venue.address}"
    else:
        return None
    return f"https://www.google.com/maps/dir/?api=1&destination={quote(destination)}"


@dataclass(frozen=True, slots=True)
class Places:
    """City slugs, timezones and area names, looked up once for a list of venues."""

    cities: dict[uuid.UUID, City]
    areas: dict[uuid.UUID, str]

    @classmethod
    async def load(cls, session: AsyncSession, venues: list[Venue]) -> Places:
        city_ids = {v.city_id for v in venues}
        area_ids = {v.area_id for v in venues if v.area_id}
        cities = (
            {c.id: c for c in await session.scalars(select(City).where(City.id.in_(city_ids)))}
            if city_ids
            else {}
        )
        areas = (
            dict(
                (
                    await session.execute(select(Area.id, Area.name).where(Area.id.in_(area_ids)))
                ).all()
            )
            if area_ids
            else {}
        )
        return cls(cities, areas)

    def tz(self, venue: Venue) -> str | None:
        city = self.cities.get(venue.city_id)
        return city.timezone if city else None

    def open_status(self, venue: Venue, now: datetime | None = None) -> OpenStatus:
        return status_at(venue.opening_hours, local(now or datetime.now(UTC), self.tz(venue)))

    def brief(self, venue: Venue, now: datetime | None = None) -> VenueBriefOut:
        city = self.cities.get(venue.city_id)
        status = self.open_status(venue, now)
        return VenueBriefOut(
            venue_code=venue.venue_code,
            name=venue.name,
            slug=venue.slug,
            city_slug=city.slug if city else "",
            category=venue.category,
            area=self.areas.get(venue.area_id) if venue.area_id else None,
            photo_url=photo_url(venue.photo_s3_key),
            open=OpenStatusOut(
                is_open=status.is_open,
                label=status.label,
                closes_at=status.closes_at,
                opens_at=status.opens_at,
            ),
        )


async def regular_now(session: AsyncSession, venue: Venue, tz: str | None) -> RegularNowOut | None:
    """A8's "Regular right now: Riya S. (7)": the customer with the most visits here this month, if
    they reached Regular and chose to be public (A8). Nobody private is ever named."""
    month = month_of(local(datetime.now(UTC), tz).date())
    row = (
        await session.execute(
            select(User.name, User.is_public, func.count().label("visits"))
            .select_from(Visit)
            .join(User, User.id == Visit.user_id)
            .where(
                Visit.venue_id == venue.id,
                Visit.status.in_(COUNTED_STATUSES),
                Visit.visit_date.between(month.start, month.end),
            )
            .group_by(User.id)
            .order_by(func.count().desc(), func.max(Visit.visited_at).asc())
            .limit(1)
        )
    ).one_or_none()
    if row is None or row.visits < badges.REGULAR_VISITS or not row.is_public:
        return None
    return RegularNowOut(name=public_name(row.name, True), visits=row.visits)


async def venue_page(session: AsyncSession, venue: Venue) -> VenuePageOut:
    places = await Places.load(session, [venue])
    tz = places.tz(venue)
    today = local(datetime.now(UTC), tz).date()
    trending_ids = {
        t.venue.id for t in await rankings.trending(session, venue.city_id, today, limit=10)
    }
    brief = places.brief(venue)
    spots_left = await badges.first_notch_spots_left(session, venue)
    return VenuePageOut(
        **brief.model_dump(),
        description=venue.description,
        address=venue.address,
        landmark=venue.landmark,
        latitude=venue.latitude,
        longitude=venue.longitude,
        opening_hours=venue.opening_hours,
        tags=venue.tags or [],
        directions_url=directions_url(venue),
        trending=venue.id in trending_ids,
        first_notch_spots_left=spots_left,
        first_notch_spots=venue.first_notch_spots,
        regular_visits=badges.REGULAR_VISITS,
        regular_now=await regular_now(session, venue, tz),
        founding_venue_no=venue.founding_venue_no,
    )
