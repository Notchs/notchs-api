"""The customer's passport on the website: who they are, their Notchs and the monthly reward."""

from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.schemas import PaginatedResponse
from app.common.time import ist_date
from app.modules.locations.models import Area, City
from app.modules.passport import rewards
from app.modules.passport.exceptions import PassportAreaInvalidException
from app.modules.passport.repository import PassportRepository
from app.modules.passport.schemas import MeOut, MeUpdate, NotchOut, PlaceRef, RewardOut, Totals
from app.modules.scans.niches import niche_of
from app.modules.users.models import User


def _month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


class PassportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = PassportRepository(session)

    async def me(self, user: User) -> MeOut:
        city = await self.session.get(City, user.city_id) if user.city_id else None
        area = await self.session.get(Area, user.area_id) if user.area_id else None
        notches, places, points = await self.repository.totals(user.id)
        return MeOut(
            name=user.name,
            handle=user.handle,
            city=PlaceRef(id=city.id, name=city.name) if city else None,
            area=PlaceRef(id=area.id, name=area.name) if area else None,
            is_public=user.is_public,
            theme=user.theme,
            founding_member_no=user.founding_member_no,
            instagram_handle=user.instagram_handle,
            show_instagram_to_venues=user.show_instagram_to_venues,
            member_since=user.created_at,
            totals=Totals(notches=notches, places=places, points=points),
        )

    async def update_me(self, user: User, body: MeUpdate) -> MeOut:
        changes = body.model_dump(exclude_unset=True)
        if "area_id" in changes:
            area_id = changes.pop("area_id")
            if area_id is None:
                user.area_id = None
            else:
                # Only an area of the customer's city (any city before their first scan).
                area = await self.repository.area_in_city(area_id, user.city_id)
                if area is None:
                    raise PassportAreaInvalidException()
                user.area_id = area.id
                user.city_id = area.city_id
        for field in ("name", "theme", "show_instagram_to_venues"):
            if changes.get(field) is not None:
                setattr(user, field, changes[field])
        if "instagram_handle" in changes:
            user.instagram_handle = changes["instagram_handle"]
            if user.instagram_handle is None:
                user.show_instagram_to_venues = False
        await self.session.flush()
        return await self.me(user)

    async def notches(self, user: User, page: int, size: int) -> PaginatedResponse[NotchOut]:
        rows, total = await self.repository.notches(user.id, offset=(page - 1) * size, limit=size)
        items = []
        for row in rows:
            niche = niche_of(row.category)
            items.append(
                NotchOut(
                    notch_no=row.notch_no,
                    venue_name=row.venue_name,
                    venue_area=row.venue_area,
                    niche=row.category,
                    colour=niche.colour,
                    visit_date=row.visit_date,
                    visited_at=row.visited_at,
                    visit_no_at_venue=row.visit_no_at_venue,
                    points=row.points,
                )
            )
        return PaginatedResponse.build(items, total, page, size)

    async def reward(self, user: User, now: datetime | None = None) -> RewardOut:
        today = ist_date(now or datetime.now(UTC))
        start, end = _month_bounds(today)
        new_cafes = await self.repository.first_visits_between(
            user.id, rewards.REWARD_CATEGORY, start, end
        )
        return RewardOut(
            month=start.strftime("%Y-%m"),
            new_cafes=new_cafes,
            needed=rewards.NEW_CAFES_NEEDED,
            eligible=new_cafes >= rewards.NEW_CAFES_NEEDED,
            value_rupees=rewards.REWARD_VALUE_RUPEES,
        )
