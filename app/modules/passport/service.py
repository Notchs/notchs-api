"""The passport's screens (C1-C14), for the logged-in customer."""

import math
import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit
from app.modules.cards.builder import unnotched_count
from app.modules.cards.models import NotchCard
from app.modules.locations.models import Area, City
from app.modules.locations.time import (
    Period,
    local,
    month_from_string,
    month_label,
    month_of,
    today,
    week_of,
)
from app.modules.notches import badges as badge_rules
from app.modules.notches.models import Badge, NotchFlag, UserBadge
from app.modules.passport.exceptions import (
    AreaInvalidException,
    MonthInvalidException,
    NoCityException,
    NotchNotFoundException,
    ReportRateLimitedException,
)
from app.modules.passport.schemas import (
    AreaOut,
    BadgeOut,
    BadgesOut,
    BusiestMonthOut,
    CityOut,
    FirstNotchOut,
    HistoryOut,
    HomeOut,
    LeaderboardOut,
    LeaderRowOut,
    MeOut,
    MeUpdate,
    NextStepOut,
    NotchOut,
    PassportVenueOut,
    PlaceOut,
    PlacesOut,
    ProgressOut,
    RankOut,
    TopPlaceOut,
    UnnotchedOut,
    YearOut,
    YourVisitsOut,
)
from app.modules.passport.schemas import Period as PeriodName
from app.modules.rankings import service as rankings
from app.modules.scans.models import COUNTED_STATUSES, Visit, VisitStatus
from app.modules.users.display import first_name
from app.modules.users.founding import seats_left
from app.modules.users.models import MAX_FOUNDING_MEMBER_NO, User
from app.modules.venues.exceptions import VenueNotFoundException
from app.modules.venues.models import Venue, VenueStatus
from app.modules.venues.page import Places, venue_page

MAX_REPORTS_PER_DAY = 5
EARTH_RADIUS_M = 6_371_000
_COUNTED = Visit.status.in_(COUNTED_STATUSES)


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    """Great-circle distance (haversine), good to a few metres at city scale."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * EARTH_RADIUS_M * math.asin(math.sqrt(a)))


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def regular_text(left: int) -> str:
    return f"{_plural(left, 'more visit')} to become the Regular here."


class PassportService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self.session = session
        self.redis = redis

    # --- me (C1, C11, C12) ---------------------------------------------------------------

    async def home_city(self, user: User) -> City | None:
        """The city from the customer's first scan, else their area's, else the first live one."""
        if user.city_id:
            return await self.session.get(City, user.city_id)
        if user.area_id:
            area = await self.session.get(Area, user.area_id)
            if area:
                return await self.session.get(City, area.city_id)
        return await self.session.scalar(
            select(City).where(City.is_active.is_(True)).order_by(City.name).limit(1)
        )

    async def _city(self, user: User) -> City:
        city = await self.home_city(user)
        if city is None:
            raise NoCityException()
        return city

    async def me(self, user: User) -> MeOut:
        city = await self.home_city(user)
        area = await self.session.get(Area, user.area_id) if user.area_id else None
        first = await self.session.scalar(
            select(Visit)
            .where(Visit.user_id == user.id, _COUNTED)
            .order_by(Visit.visited_at)
            .limit(1)
        )
        first_out = None
        if first is not None:
            venue = await self.session.get(Venue, first.venue_id)
            places = await Places.load(self.session, [venue])
            first_out = FirstNotchOut(
                venue=places.brief(venue),
                scanned_at=first.visited_at,
                time=local(first.visited_at, places.tz(venue)).strftime("%H:%M"),
            )
        return MeOut(
            id=user.id,
            user_code=user.user_code,
            name=user.name,
            needs_name=not user.name,
            area=AreaOut(id=area.id, name=area.name) if area else None,
            city=CityOut(id=city.id, name=city.name, slug=city.slug) if city else None,
            founding_member_no=user.founding_member_no,
            founding_member_total=MAX_FOUNDING_MEMBER_NO,
            theme=user.theme,
            is_public=user.is_public,
            notches_total=await self._count(Visit.user_id == user.id),
            badges_count=await self._badges_count(user),
            cards_count=await self.session.scalar(
                select(func.count()).where(NotchCard.user_id == user.id)
            )
            or 0,
            first_notch=first_out,
            joined_at=user.created_at,
        )

    async def _badges_count(self, user: User) -> int:
        have = await badge_rules.earned(self.session, user.id)
        reserved = Badge.FOUNDING_MEMBER not in have and user.founding_member_no is not None
        return len(have) + int(reserved)

    async def update_me(self, user: User, body: MeUpdate) -> MeOut:
        sent = body.model_fields_set
        if "area_id" in sent and body.area_id is not None:
            area = await self.session.get(Area, body.area_id)
            city = await self.home_city(user)
            if area is None or (city is not None and area.city_id != city.id):
                raise AreaInvalidException()
        for field in ("name", "area_id", "theme", "is_public"):
            if field in sent:
                setattr(user, field, getattr(body, field))
        await self.session.flush()
        return await self.me(user)

    # --- home (C2) -----------------------------------------------------------------------

    async def home(self, user: User) -> HomeOut:
        city = await self._city(user)
        day = today(city.timezone)
        month = month_of(day)
        count = await self._count(
            Visit.user_id == user.id, Visit.visit_date.between(month.start, month.end)
        )
        rank = None
        notches_to_rank = None
        if count >= rankings.RANK_FROM_NOTCHES:
            standing = await rankings.standing(self.session, user.id, city.id, month)
            if standing is not None:
                rank = RankOut(
                    position=standing.rank,
                    label=f"#{standing.rank} in {city.name}",
                    top_percent=standing.top_percent,
                )
        elif count > 0:
            notches_to_rank = rankings.RANK_FROM_NOTCHES - count
        return HomeOut(
            first_name=first_name(user.name),
            month=month.start,
            month_label=month_label(month.start),
            notches_this_month=count,
            rank=rank,
            notches_to_rank=notches_to_rank,
            next_step=await self._next_step(user, month),
            unnotched=await self._unnotched_pick(user, city, day),
        )

    async def _next_step(self, user: User, month: Period) -> NextStepOut | None:
        """The place closest to Regular this month that isn't there yet."""
        row = (
            await self.session.execute(
                select(Visit.venue_id, func.count().label("visits"))
                .where(
                    Visit.user_id == user.id,
                    _COUNTED,
                    Visit.visit_date.between(month.start, month.end),
                )
                .group_by(Visit.venue_id)
                .having(func.count() < badge_rules.REGULAR_VISITS)
                .order_by(func.count().desc(), func.max(Visit.visited_at).desc())
                .limit(1)
            )
        ).one_or_none()
        if row is None:
            return None
        venue = await self.session.get(Venue, row.venue_id)
        if venue is None or venue.deleted_at is not None:
            return None
        places = await Places.load(self.session, [venue])
        left = badge_rules.REGULAR_VISITS - row.visits
        return NextStepOut(
            venue=places.brief(venue),
            visits=row.visits,
            target=badge_rules.REGULAR_VISITS,
            left=left,
            text=regular_text(left),
        )

    async def _unnotched_pick(self, user: User, city: City, day: date) -> UnnotchedOut | None:
        """ONE place they have never notched: trending first, then First Notch spots."""
        mine = set(
            await self.session.scalars(
                select(distinct(Visit.venue_id)).where(Visit.user_id == user.id, _COUNTED)
            )
        )
        for count in await rankings.trending(self.session, city.id, day, limit=10):
            if count.venue.id not in mine:
                places = await Places.load(self.session, [count.venue])
                return UnnotchedOut(venue=places.brief(count.venue), tag="Trending this week")
        conditions = [
            Venue.city_id == city.id,
            Venue.status == VenueStatus.COUNTER_LIVE,
            Venue.deleted_at.is_(None),
        ]
        if mine:
            conditions.append(Venue.id.not_in(mine))
        candidates = (
            await self.session.scalars(
                select(Venue).where(*conditions).order_by(Venue.created_at.desc()).limit(10)
            )
        ).all()
        for venue in candidates:
            if await badge_rules.first_notch_spots_left(self.session, venue) > 0:
                places = await Places.load(self.session, [venue])
                return UnnotchedOut(venue=places.brief(venue), tag="First Notch spots left")
        if candidates:
            places = await Places.load(self.session, [candidates[0]])
            return UnnotchedOut(venue=places.brief(candidates[0]), tag=None)
        return None

    # --- history (C3, C4) ----------------------------------------------------------------

    async def history(self, user: User, month: str | None) -> HistoryOut:
        city = await self.home_city(user)
        tz = city.timezone if city else None
        try:
            period = month_from_string(month) if month else month_of(today(tz))
        except ValueError as exc:
            raise MonthInvalidException() from exc
        notches = (
            await self.session.scalars(
                select(Visit)
                .where(
                    Visit.user_id == user.id,
                    _COUNTED,
                    Visit.visit_date.between(period.start, period.end),
                )
                .order_by(Visit.visited_at.desc())
            )
        ).all()
        items = await self._notch_outs(notches)
        all_time = (
            await self.session.execute(
                select(func.count(), func.count(distinct(Visit.venue_id))).where(
                    Visit.user_id == user.id, _COUNTED
                )
            )
        ).one()
        return HistoryOut(
            month=period.start,
            month_label=month_label(period.start),
            total=len(items),
            items=items,
            all_time_notches=all_time[0],
            all_time_places=all_time[1],
        )

    async def notch(self, user: User, code: str) -> NotchOut:
        notch = await self._own_notch(user, code)
        return (await self._notch_outs([notch]))[0]

    async def report(self, user: User, code: str, message: str) -> NotchOut:
        """C4's "Report a mistake": the Notch goes to the review queue with the message."""
        # Locked: two reports at once update the Notch one after the other.
        notch = await self._own_notch(user, code, for_update=True)
        if not await rate_limit.hit(
            self.redis,
            f"rl:notch-report:{user.id}",
            limit=MAX_REPORTS_PER_DAY,
            window_seconds=24 * 3600,
        ):
            raise ReportRateLimitedException()
        if NotchFlag.CUSTOMER_REPORT not in notch.flags:
            notch.flags = [*notch.flags, NotchFlag.CUSTOMER_REPORT]
        note = f"Customer report: {message.strip()}"
        if note not in (notch.flag_note or ""):  # a double tap or retry adds it once
            notch.flag_note = f"{notch.flag_note} {note}" if notch.flag_note else note
        if notch.status == VisitStatus.VALID:
            notch.status = VisitStatus.FLAGGED
        await self.session.flush()
        return (await self._notch_outs([notch]))[0]

    async def _own_notch(self, user: User, code: str, *, for_update: bool = False) -> Visit:
        query = select(Visit).where(
            Visit.notch_code == code.strip().upper(), Visit.user_id == user.id
        )
        notch = await self.session.scalar(query.with_for_update() if for_update else query)
        if notch is None:
            raise NotchNotFoundException()
        return notch

    async def _notch_outs(self, notches: Sequence[Visit]) -> list[NotchOut]:
        venues = (
            {
                v.id: v
                for v in await self.session.scalars(
                    select(Venue).where(Venue.id.in_({n.venue_id for n in notches}))
                )
            }
            if notches
            else {}
        )
        places = await Places.load(self.session, list(venues.values()))
        out = []
        for notch in notches:
            venue = venues[notch.venue_id]
            out.append(
                NotchOut(
                    notch_code=notch.notch_code,
                    venue=places.brief(venue),
                    scanned_at=notch.visited_at,
                    local_date=notch.visit_date,
                    time=local(notch.visited_at, places.tz(venue)).strftime("%H:%M"),
                    notch_no=notch.notch_no,
                    status=notch.status,
                    under_review=notch.status == VisitStatus.FLAGGED,
                )
            )
        return out

    # --- badges (C8) ---------------------------------------------------------------------

    async def badges(self, user: User) -> BadgesOut:
        city = await self.home_city(user)
        day = today(city.timezone if city else None)
        have = await badge_rules.earned(self.session, user.id)
        venue_ids = {b.venue_id for b in have.values() if b.venue_id}
        venues = (
            {
                v.id: v
                for v in await self.session.scalars(select(Venue).where(Venue.id.in_(venue_ids)))
            }
            if venue_ids
            else {}
        )
        places = await Places.load(self.session, list(venues.values()))

        earned, locked = [], []
        for badge, info in badge_rules.BADGES.items():
            row = have.get(badge)
            if row is None and badge == Badge.FOUNDING_MEMBER and user.founding_member_no:
                # Reserved on the website, before any Notch: the number is already theirs.
                row = UserBadge(
                    user_id=user.id, badge=badge, earned_at=user.web_joined_at or user.created_at
                )
            if row is not None:
                venue = venues.get(row.venue_id) if row.venue_id else None
                earned.append(
                    BadgeOut(
                        badge=badge,
                        name=info.name,
                        how=info.how,
                        earned=True,
                        earned_at=row.earned_at,
                        venue=places.brief(venue) if venue else None,
                        detail=(
                            f"#{user.founding_member_no} of {MAX_FOUNDING_MEMBER_NO}"
                            if badge == Badge.FOUNDING_MEMBER and user.founding_member_no
                            else None
                        ),
                        progress=None,
                    )
                )
            else:
                locked.append(await self._locked(user, badge, day))
        return BadgesOut(earned=earned, locked=locked)

    async def _locked(self, user: User, badge: Badge, day: date) -> BadgeOut:
        info = badge_rules.BADGES[badge]
        progress = None
        detail = None
        venue_out = None
        match badge:
            case Badge.REGULAR:
                row = (
                    await self.session.execute(
                        select(Visit.venue_id, func.count().label("visits"))
                        .where(
                            Visit.user_id == user.id,
                            _COUNTED,
                            Visit.visit_date.between(month_of(day).start, month_of(day).end),
                        )
                        .group_by(Visit.venue_id)
                        .order_by(func.count().desc(), func.max(Visit.visited_at).desc())
                        .limit(1)
                    )
                ).one_or_none()
                progress = ProgressOut(
                    current=row.visits if row else 0, target=badge_rules.REGULAR_VISITS
                )
                if row is not None:
                    venue = await self.session.get(Venue, row.venue_id)
                    venue_out = (await Places.load(self.session, [venue])).brief(venue)
            case Badge.NIGHT_OWL:
                progress = ProgressOut(
                    current=await badge_rules.night_visits(self.session, user.id),
                    target=badge_rules.NIGHT_OWL_VISITS,
                )
            case Badge.CAFE_HOPPER:
                progress = ProgressOut(
                    current=await badge_rules.cafes_notched(self.session, user.id),
                    target=badge_rules.CAFE_HOPPER_CAFES,
                )
            case Badge.FOUNDING_MEMBER:
                left = await seats_left(self.session)
                detail = f"{left} of {MAX_FOUNDING_MEMBER_NO} left" if left else "All taken"
            case Badge.FIRST_NOTCH:
                detail = "Look for “First Notch spots left” in Places."
        return BadgeOut(
            badge=badge,
            name=info.name,
            how=info.how,
            earned=False,
            earned_at=None,
            venue=venue_out,
            detail=detail,
            progress=progress,
        )

    # --- leaderboard (C7) ----------------------------------------------------------------

    async def leaderboard(
        self,
        user: User,
        period_name: PeriodName,
        *,
        category: str | None = None,
        area_id: uuid.UUID | None = None,
    ) -> LeaderboardOut:
        city = await self._city(user)
        day = today(city.timezone)
        period = {"week": week_of(day), "month": month_of(day), "all": None}[period_name]
        categories = [category] if category else None
        standing = await rankings.standing(
            self.session, user.id, city.id, period, categories=categories, area_id=area_id
        )
        top = await rankings.top(
            self.session, city.id, period, categories=categories, area_id=area_id
        )
        area_name = (
            await self.session.scalar(select(Area.name).where(Area.id == user.area_id))
            if user.area_id
            else None
        )
        active = (
            standing.active_users
            if standing
            else await rankings.active_users(self.session, city.id, period)
        )
        return LeaderboardOut(
            period=period_name,
            city=CityOut(id=city.id, name=city.name, slug=city.slug),
            starts_on=period.start if period else None,
            ends_on=period.end if period else None,
            you=(
                LeaderRowOut(
                    rank=standing.rank,
                    name="You",
                    area=area_name,
                    notches=standing.notches,
                    is_you=True,
                )
                if standing
                else None
            ),
            top=[
                LeaderRowOut(
                    rank=row.rank,
                    name="You" if row.user_id == user.id else row.name,
                    area=row.area,
                    notches=row.notches,
                    is_you=row.user_id == user.id,
                )
                for row in top
            ],
            active_users=active,
            top_percent=standing.top_percent if standing else None,
        )

    # --- places (C5) and venue (C6) ------------------------------------------------------

    async def places(
        self,
        user: User,
        *,
        lat: float | None = None,
        lng: float | None = None,
        area_id: uuid.UUID | None = None,
        category: str | None = None,
        open_now: bool = False,
    ) -> PlacesOut:
        city = await self._city(user)
        day = today(city.timezone)
        query = select(Venue).where(
            Venue.city_id == city.id,
            Venue.status == VenueStatus.COUNTER_LIVE,
            Venue.deleted_at.is_(None),
        )
        area = None
        if area_id is not None:
            area = await self.session.get(Area, area_id)
            if area is None or area.city_id != city.id:
                raise AreaInvalidException()
            query = query.where(Venue.area_id == area_id)
        if category:
            query = query.where(Venue.category == category)
        venues = list(await self.session.scalars(query.order_by(Venue.name)))
        places = await Places.load(self.session, venues)

        month = month_of(day)
        visits = dict(
            (
                await self.session.execute(
                    select(Visit.venue_id, func.count())
                    .where(
                        Visit.user_id == user.id,
                        _COUNTED,
                        Visit.visit_date.between(month.start, month.end),
                    )
                    .group_by(Visit.venue_id)
                )
            ).all()
        )
        ever = set(
            await self.session.scalars(
                select(distinct(Visit.venue_id)).where(Visit.user_id == user.id, _COUNTED)
            )
        )
        trending_ids = {
            t.venue.id for t in await rankings.trending(self.session, city.id, day, limit=10)
        }
        near = lat is not None and lng is not None
        items = []
        for venue in venues:
            brief = places.brief(venue)
            if open_now and brief.open.is_open is False:
                continue
            items.append(
                PlaceOut(
                    venue=brief,
                    distance_m=(
                        distance_m(lat, lng, float(venue.latitude), float(venue.longitude))
                        if near and venue.latitude is not None and venue.longitude is not None
                        else None
                    ),
                    tag=await self._place_tag(venue, visits.get(venue.id, 0), ever, trending_ids),
                )
            )
        if near:
            items.sort(key=lambda p: (p.distance_m is None, p.distance_m or 0))
        else:
            items.sort(key=lambda p: (p.venue.area or "~", p.venue.name))
        areas = await self.session.execute(
            select(Area.id, Area.name).where(Area.city_id == city.id).order_by(Area.name)
        )
        return PlacesOut(
            near_you=near,
            area=AreaOut(id=area.id, name=area.name) if area else None,
            items=items,
            areas=[AreaOut(id=i, name=n) for i, n in areas],
        )

    async def _place_tag(
        self,
        venue: Venue,
        visits_this_month: int,
        ever: set[uuid.UUID],
        trending_ids: set[uuid.UUID],
    ) -> str | None:
        if 0 < visits_this_month < badge_rules.REGULAR_VISITS:
            return f"You're {badge_rules.REGULAR_VISITS - visits_this_month} from Regular"
        if venue.id not in ever and await badge_rules.first_notch_spots_left(self.session, venue):
            return "First Notch spots left"
        if venue.id in trending_ids:
            return "Trending"
        return None

    async def venue(self, user: User, venue_code: str) -> PassportVenueOut:
        venue = await self.session.scalar(
            select(Venue).where(
                Venue.venue_code == venue_code.strip().upper(), Venue.deleted_at.is_(None)
            )
        )
        if venue is None:
            raise VenueNotFoundException()
        page = await venue_page(self.session, venue)
        tz = await self.session.scalar(select(City.timezone).where(City.id == venue.city_id))
        month = month_of(today(tz))
        mine = Visit.user_id == user.id, Visit.venue_id == venue.id, _COUNTED
        this_month = await self._count(*mine, Visit.visit_date.between(month.start, month.end))
        last = await self.session.scalar(select(func.max(Visit.visit_date)).where(*mine))
        return PassportVenueOut(
            **page.model_dump(),
            you=YourVisitsOut(
                visits_this_month=this_month,
                visits_total=await self._count(*mine),
                left_to_regular=max(0, badge_rules.REGULAR_VISITS - this_month),
                is_regular_this_month=this_month >= badge_rules.REGULAR_VISITS,
                last_visit_on=last,
            ),
        )

    # --- year (C14) ----------------------------------------------------------------------

    async def year(self, user: User, year: int) -> YearOut:
        city = await self._city(user)
        period = Period(date(year, 1, 1), date(year, 12, 31))
        rows = (
            await self.session.execute(
                select(Visit.venue_id, Venue.name, Visit.visit_date)
                .join(Venue, Venue.id == Visit.venue_id)
                .where(
                    Visit.user_id == user.id,
                    _COUNTED,
                    Visit.visit_date.between(period.start, period.end),
                )
            )
        ).all()
        per_venue = Counter((row.venue_id, row.name) for row in rows)
        per_month = Counter(row.visit_date.replace(day=1) for row in rows)
        standing = await rankings.standing(self.session, user.id, city.id, period) if rows else None
        top_place = per_venue.most_common(1)[0] if per_venue else None
        busiest = per_month.most_common(1)[0] if per_month else None
        return YearOut(
            year=year,
            city=city.name,
            name=user.name,
            notches=len(rows),
            places=len(per_venue),
            place_of_the_year=(
                TopPlaceOut(name=top_place[0][1], visits=top_place[1]) if top_place else None
            ),
            busiest_month=(
                BusiestMonthOut(month_label=month_label(busiest[0]), notches=busiest[1])
                if busiest
                else None
            ),
            top_percent=standing.top_percent if standing else None,
            city_rank=(
                standing.rank
                if standing and standing.notches >= rankings.RANK_FROM_NOTCHES
                else None
            ),
            unnotched_count=await unnotched_count(self.session, user.id, city.id),
        )

    # --- data download (C12) -------------------------------------------------------------

    async def export(self, user: User) -> dict[str, Any]:
        """Everything Notchs holds about the customer, as plain JSON."""
        notches = (
            await self.session.scalars(
                select(Visit).where(Visit.user_id == user.id).order_by(Visit.visited_at)
            )
        ).all()
        venue_names = (
            dict(
                (
                    await self.session.execute(
                        select(Venue.id, Venue.name).where(
                            Venue.id.in_({n.venue_id for n in notches})
                        )
                    )
                ).all()
            )
            if notches
            else {}
        )
        badges = await badge_rules.earned(self.session, user.id)
        cards = await self.session.scalars(
            select(NotchCard).where(NotchCard.user_id == user.id).order_by(NotchCard.period)
        )
        return {
            "exported_at": datetime.now(UTC).isoformat(),
            "profile": {
                "user_code": user.user_code,
                "phone": user.phone_e164,
                "name": user.name,
                "is_public": user.is_public,
                "theme": user.theme,
                "founding_member_no": user.founding_member_no,
                "joined_at": user.created_at.isoformat(),
                "age_confirmed_at": _iso(user.age_confirmed_at),
                "consent_at": _iso(user.consent_at),
                "consent_version": user.consent_version,
                "web_joined_at": _iso(user.web_joined_at),
            },
            "notches": [
                {
                    "notch_code": n.notch_code,
                    "venue": venue_names.get(n.venue_id),
                    "scanned_at": n.visited_at.isoformat(),
                    "notch_no": n.notch_no,
                    "status": n.status,
                }
                for n in notches
            ],
            "badges": [
                {"badge": b.value, "earned_at": row.earned_at.isoformat()}
                for b, row in badges.items()
            ],
            "cards": [
                {
                    "card_code": c.card_code,
                    "month": c.period.isoformat(),
                    "notches": c.notch_count,
                    "shared": c.shared_at is not None,
                }
                for c in cards
            ],
        }

    async def _count(self, *conditions: Any) -> int:
        return await self.session.scalar(select(func.count()).where(_COUNTED, *conditions)) or 0


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None
