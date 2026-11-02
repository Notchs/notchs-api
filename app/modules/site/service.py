"""The public website's data (A1-A15)."""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import ColumnElement, Date, cast, distinct, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ids import new_code, new_uuid
from app.core import rate_limit
from app.modules.locations.models import Area, City
from app.modules.locations.time import Period, month_label, month_of, today, week_of
from app.modules.rankings import service as rankings
from app.modules.scans.models import COUNTED_STATUSES, Visit, VisitStatus
from app.modules.site.exceptions import (
    CategoryNotFoundException,
    CityNotFoundException,
    JoinRateLimitedException,
    WeekInvalidException,
)
from app.modules.site.schemas import (
    AreaLinkOut,
    CategoryTileOut,
    CityPageOut,
    CityStatsOut,
    JoinIn,
    JoinOut,
    PulseMonthOut,
    PulseOut,
    RankingOut,
    RankingRowOut,
    RankingSort,
    StatsOut,
    TrendingOut,
)
from app.modules.users import founding as founding_members
from app.modules.users.models import MAX_FOUNDING_MEMBER_NO, SignupSource, User, UserStatus
from app.modules.venues import founding as founding_venues
from app.modules.venues.exceptions import VenueNotFoundException
from app.modules.venues.models import MAX_FOUNDING_VENUE_NO, Venue, VenueCategory, VenueStatus
from app.modules.venues.page import Places, venue_page
from app.modules.venues.schemas import VenuePageOut

MAX_JOINS_PER_IP_PER_HOUR = 10
FEW_PLACES = 5
PAST_WEEKS = 8


@dataclass(frozen=True, slots=True)
class CategoryPage:
    label: str
    categories: tuple[VenueCategory, ...]


# The A7 pages: /ahmedabad/<slug>. A6's three tiles come first.
CATEGORY_PAGES: dict[str, CategoryPage] = {
    "cafes": CategoryPage("Cafés", (VenueCategory.CAFE,)),
    "restaurants-and-bars": CategoryPage(
        "Restaurants & bars", (VenueCategory.RESTAURANT, VenueCategory.BAR)
    ),
    "turfs": CategoryPage("Turfs & gyms", (VenueCategory.TURF_GYM,)),
    "restaurants": CategoryPage("Restaurants", (VenueCategory.RESTAURANT,)),
    "bars": CategoryPage("Bars", (VenueCategory.BAR,)),
    "hotels": CategoryPage("Hotels", (VenueCategory.HOTEL,)),
}
CITY_TILES = ("cafes", "restaurants-and-bars", "turfs")


def _live(city_id: uuid.UUID) -> list[ColumnElement[bool]]:
    return [
        Venue.city_id == city_id,
        Venue.status == VenueStatus.COUNTER_LIVE,
        Venue.deleted_at.is_(None),
    ]


class SiteService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self.session = session
        self.redis = redis

    # --- join (A1 stop 5, A14) -----------------------------------------------------------

    async def join(self, body: JoinIn, *, ip: str | None) -> JoinOut:
        """Reserve a Founding Member number for a WhatsApp number. The number is not verified
        here, so this never counts as consent: the bot still asks on the first scan (B1).

        Double clicks, retries and two tabs make one customer: the insert does nothing when the
        number is already there (the phone number is unique). A number already on the list gets
        the same answer with no number, and its details are only filled in, never changed.
        """
        if ip and not await rate_limit.hit(
            self.redis, f"rl:join:{ip}", limit=MAX_JOINS_PER_IP_PER_HOUR, window_seconds=3600
        ):
            raise JoinRateLimitedException()
        area = await self.session.get(Area, body.area_id) if body.area_id else None
        now = datetime.now(UTC)
        new_id = await self.session.scalar(
            insert(User)
            .values(
                id=new_uuid(),
                user_code=new_code("USR"),
                phone_e164=body.phone,
                name=body.name,
                area_id=area.id if area else None,
                city_id=area.city_id if area else None,
                signup_source=SignupSource.WEB,
                web_joined_at=now,
            )
            .on_conflict_do_nothing(index_elements=[User.phone_e164])
            .returning(User.id)
        )
        user = await self.session.scalar(
            select(User).where(User.phone_e164 == body.phone).with_for_update()
        )
        assert user is not None
        number = None
        if new_id is not None:
            number = await founding_members.assign(self.session, user)
        elif user.status == UserStatus.ACTIVE and user.deleted_at is None:
            user.name = user.name or body.name
            if user.area_id is None and area is not None:
                user.area_id = area.id
            user.web_joined_at = user.web_joined_at or now
            await self.session.flush()
        return JoinOut(
            founding_member_no=number,
            founding_members_left=await founding_members.seats_left(self.session),
        )

    async def stats(self) -> StatsOut:
        return StatsOut(
            founding_members_left=await founding_members.seats_left(self.session),
            founding_members_total=MAX_FOUNDING_MEMBER_NO,
            founding_venues_left=await founding_venues.seats_left(self.session),
            founding_venues_total=MAX_FOUNDING_VENUE_NO,
        )

    # --- city (A6) and rankings (A7) -----------------------------------------------------

    async def _city(self, slug: str) -> City:
        city = await self.session.scalar(
            select(City).where(City.slug == slug.lower(), City.is_active.is_(True))
        )
        if city is None:
            raise CityNotFoundException()
        return city

    async def city_page(self, slug: str) -> CityPageOut:
        city = await self._city(slug)
        day = today(city.timezone)
        live_by_category = dict(
            (
                await self.session.execute(
                    select(Venue.category, func.count())
                    .where(*_live(city.id))
                    .group_by(Venue.category)
                )
            ).all()
        )
        trending = await rankings.trending(self.session, city.id, day)
        places = await Places.load(self.session, [t.venue for t in trending])
        areas = await self.session.execute(
            select(Area.name, Area.slug, func.count(Venue.id))
            .outerjoin(
                Venue,
                (Venue.area_id == Area.id)
                & (Venue.status == VenueStatus.COUNTER_LIVE)
                & Venue.deleted_at.is_(None),
            )
            .where(Area.city_id == city.id)
            .group_by(Area.id)
            .order_by(Area.name)
        )
        month = month_of(day)
        month_counts = (
            await self.session.execute(
                select(func.count(), func.count(distinct(Visit.user_id))).where(
                    Visit.city_id == city.id,
                    Visit.status.in_(COUNTED_STATUSES),
                    Visit.visit_date.between(month.start, month.end),
                )
            )
        ).one()
        return CityPageOut(
            name=city.name,
            slug=city.slug,
            has_rankings=await self._has_full_week(city, day),
            categories=[
                CategoryTileOut(
                    slug=slug_,
                    label=CATEGORY_PAGES[slug_].label,
                    venues_live=sum(
                        live_by_category.get(c, 0) for c in CATEGORY_PAGES[slug_].categories
                    ),
                )
                for slug_ in CITY_TILES
            ],
            trending=[
                TrendingOut(venue=places.brief(t.venue), visits_this_week=t.visits)
                for t in trending
            ],
            areas=[AreaLinkOut(name=n, slug=s, venues_live=c) for n, s, c in areas],
            stats=CityStatsOut(
                month_label=month_label(month.start),
                notches_this_month=month_counts[0],
                explorers_this_month=month_counts[1],
                venues_live=sum(live_by_category.values()),
            ),
        )

    async def _first_notch_day(self, city: City) -> date | None:
        return await self.session.scalar(
            select(func.min(Visit.visit_date)).where(
                Visit.city_id == city.id, Visit.status.in_(COUNTED_STATUSES)
            )
        )

    async def _has_full_week(self, city: City, day: date) -> bool:
        first = await self._first_notch_day(city)
        return first is not None and first < week_of(day).start

    async def ranking(
        self,
        city_slug: str,
        category_slug: str,
        *,
        sort: RankingSort = "most_visited",
        area_slug: str | None = None,
        week_start: date | None = None,
    ) -> RankingOut:
        city = await self._city(city_slug)
        page = CATEGORY_PAGES.get(category_slug.lower())
        if page is None:
            raise CategoryNotFoundException(details={"available": list(CATEGORY_PAGES)})
        day = today(city.timezone)
        latest = week_of(day - timedelta(days=7))  # the last full week, Monday to Sunday
        if week_start is None:
            week = latest
        elif week_start.weekday() != 0 or week_start > latest.start:
            raise WeekInvalidException()
        else:
            week = week_of(week_start)
        area = None
        if area_slug:
            area = await self.session.scalar(
                select(Area).where(Area.city_id == city.id, Area.slug == area_slug.lower())
            )
            if area is None:
                raise CityNotFoundException("That area is not in this city.")
        counts = await rankings.venue_ranking(
            self.session,
            city.id,
            week,
            categories=page.categories,
            area_id=area.id if area else None,
            trending_first=sort == "trending",
        )
        places = await Places.load(self.session, [c.venue for c in counts])
        live_in_area = None
        if area is not None:
            live_in_area = await self.session.scalar(
                select(func.count()).where(*_live(city.id), Venue.area_id == area.id)
            )
        return RankingOut(
            city=city.name,
            city_slug=city.slug,
            category=category_slug.lower(),
            category_label=page.label,
            sort=sort,
            week_start=week.start,
            week_end=week.end,
            updated_on=week.end + timedelta(days=1),
            area=(
                AreaLinkOut(name=area.name, slug=area.slug, venues_live=live_in_area or 0)
                if area
                else None
            ),
            items=[
                RankingRowOut(
                    rank=i,
                    venue=places.brief(c.venue),
                    visits=c.visits,
                    previous_visits=c.previous_visits,
                )
                for i, c in enumerate(counts, start=1)
            ],
            few_places=len(counts) < FEW_PLACES,
            past_weeks=await self._past_weeks(city, week),
        )

    async def _past_weeks(self, city: City, shown: Period) -> list[date]:
        first = await self._first_notch_day(city)
        if first is None:
            return []
        weeks = []
        start = shown.start - timedelta(days=7)
        while start >= week_of(first).start and len(weeks) < PAST_WEEKS:
            weeks.append(start)
            start -= timedelta(days=7)
        return weeks

    # --- venue page (A8) -----------------------------------------------------------------

    async def venue(self, city_slug: str, venue_slug: str) -> VenuePageOut:
        city = await self._city(city_slug)
        venue = await self.session.scalar(
            select(Venue).where(
                Venue.city_id == city.id,
                Venue.slug == venue_slug.lower(),
                Venue.deleted_at.is_(None),
                Venue.status.in_((VenueStatus.COUNTER_LIVE, VenueStatus.PAUSED)),
            )
        )
        if venue is None:
            raise VenueNotFoundException()
        return await venue_page(self.session, venue)

    # --- Notchs Pulse (A4) ---------------------------------------------------------------

    async def pulse(self) -> PulseOut:
        """Every finished month since the first Notch: visits verified, fakes blocked. Months
        follow IST, the pilot's only timezone."""
        this_month = month_of(today(None)).start
        month = cast(func.date_trunc("month", Visit.visit_date), Date).label("month")
        rows = (
            await self.session.execute(
                select(
                    month,
                    func.count().filter(Visit.status.in_(COUNTED_STATUSES)),
                    func.count().filter(Visit.status == VisitStatus.REVOKED),
                )
                .group_by(month)
                .order_by(month)
            )
        ).all()
        months = [
            PulseMonthOut(
                month=start,
                month_label=month_label(start),
                visits_verified=verified,
                fakes_blocked=rejected,
            )
            for start, verified, rejected in rows
            if start < this_month
        ]
        first_pulse = None
        if not months:
            first = rows[0][0] if rows else this_month
            first_pulse = month_of(first).end + timedelta(days=1)
        return PulseOut(months=months, first_pulse_on=first_pulse)
