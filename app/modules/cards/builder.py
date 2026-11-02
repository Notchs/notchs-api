"""The numbers on a monthly Notch Card, from the month's counted Notchs (Page 3, C9).

    count      Notchs in the month                         Face A, reveal screen 1
    place      the most-visited place and its visits       Face B, reveal screen 2
    vibe       time of day + favourite kind of place       Face C's line, reveal screen 3
               ("Late-night café hopper", from the average Notch time "22:40")
    peak       "Top 8% of Ahmedabad" (200+ active only),   Face C, reveal screen 4
               else "#38 in Ahmedabad" (from 3 Notchs)
    unnotched  live places in the city never notched       the Ember line on every face

Fewer than 3 Notchs make a simple card: no rank and no reveal, still shareable.
"""

import math
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cards.models import CardFace, NotchCard
from app.modules.locations.models import City
from app.modules.locations.time import Period, local
from app.modules.notches.badges import REGULAR_VISITS
from app.modules.notches.models import COUNTED_STATUSES, Notch
from app.modules.rankings import service as rankings
from app.modules.users.models import User
from app.modules.venues.models import Venue, VenueCategory, VenueStatus

SIMPLE_BELOW = 3
STRONG_PERCENT = 25  # a "Top X%" this good is the card's best face

# (from minute of the day, word): the last bucket whose start is <= the average time.
_TIME_WORDS = (
    (0, "Late-night"),
    (4 * 60, "Early"),
    (11 * 60, "Daytime"),
    (17 * 60, "Evening"),
    (21 * 60, "Late-night"),
)
_CATEGORY_WORDS = {
    VenueCategory.CAFE: "café hopper",
    VenueCategory.RESTAURANT: "foodie",
    VenueCategory.BAR: "bar hopper",
    VenueCategory.HOTEL: "hotel regular",
    VenueCategory.TURF_GYM: "turf regular",
    VenueCategory.OTHER: "explorer",
}


def average_minute(minutes: list[int]) -> int:
    """Mean time of day on a 24-hour circle: 23:30 and 00:30 average to 00:00, not noon."""
    angles = [m / 1440 * 2 * math.pi for m in minutes]
    x = sum(math.cos(a) for a in angles)
    y = sum(math.sin(a) for a in angles)
    if math.isclose(x, 0, abs_tol=1e-9) and math.isclose(y, 0, abs_tol=1e-9):
        return minutes[0]
    return round((math.atan2(y, x) % (2 * math.pi)) / (2 * math.pi) * 1440) % 1440


def vibe(avg_minute: int, category: str) -> str:
    word = next(w for start, w in reversed(_TIME_WORDS) if avg_minute >= start)
    noun = _CATEGORY_WORDS.get(VenueCategory(category), "explorer")
    return f"{word} {noun}"


def card_code(period: date, card_no: int) -> str:
    """NC-1026-0142: month, two-digit year, number that month."""
    return f"NC-{period.month:02d}{period.year % 100:02d}-{card_no:04d}"


@dataclass(frozen=True, slots=True)
class MonthStats:
    notch_count: int
    places_count: int
    top_venue_id: uuid.UUID
    top_venue_name: str
    top_venue_visits: int
    regular_since: date | None
    avg_time: str  # "22:40"
    vibe: str


async def month_stats(
    session: AsyncSession, user_id: uuid.UUID, period: Period, tz: str | None
) -> MonthStats | None:
    rows = (
        await session.execute(
            select(Notch.venue_id, Venue.name, Notch.category, Notch.scanned_at, Notch.local_date)
            .join(Venue, Venue.id == Notch.venue_id)
            .where(
                Notch.user_id == user_id,
                Notch.status.in_(COUNTED_STATUSES),
                Notch.local_date.between(period.start, period.end),
            )
            .order_by(Notch.scanned_at)
        )
    ).all()
    if not rows:
        return None
    visits = Counter(row.venue_id for row in rows)
    last_visit = {row.venue_id: row.scanned_at for row in rows}
    top_id = max(visits, key=lambda v: (visits[v], last_visit[v]))
    top_dates = [row.local_date for row in rows if row.venue_id == top_id]
    minutes = [(t := local(row.scanned_at, tz)).hour * 60 + t.minute for row in rows]
    avg = average_minute(minutes)
    favourite = Counter(row.category for row in rows).most_common(1)[0][0]
    return MonthStats(
        notch_count=len(rows),
        places_count=len(visits),
        top_venue_id=top_id,
        top_venue_name=next(row.name for row in rows if row.venue_id == top_id),
        top_venue_visits=visits[top_id],
        regular_since=top_dates[REGULAR_VISITS - 1] if len(top_dates) >= REGULAR_VISITS else None,
        avg_time=f"{avg // 60:02d}:{avg % 60:02d}",
        vibe=vibe(avg, favourite),
    )


async def unnotched_count(session: AsyncSession, user_id: uuid.UUID, city_id: uuid.UUID) -> int:
    """Live places in the city the customer has never notched."""
    notched = select(Notch.venue_id).where(
        Notch.user_id == user_id, Notch.status.in_(COUNTED_STATUSES)
    )
    return (
        await session.scalar(
            select(func.count()).where(
                Venue.city_id == city_id,
                Venue.status == VenueStatus.COUNTER_LIVE,
                Venue.deleted_at.is_(None),
                Venue.id.not_in(notched),
            )
        )
        or 0
    )


def best_face(card: NotchCard) -> CardFace:
    if card.rank_percent is not None and card.rank_percent <= STRONG_PERCENT:
        return CardFace.RANK
    if card.top_venue_visits >= REGULAR_VISITS:
        return CardFace.PLACE
    return CardFace.COUNT


async def build_card(
    session: AsyncSession, user: User, city: City, period: Period, card_no: int
) -> NotchCard | None:
    """The card for `user`'s month in `city`, not yet added to the session. None without
    Notchs in the month."""
    stats = await month_stats(session, user.id, period, city.timezone)
    if stats is None:
        return None
    simple = stats.notch_count < SIMPLE_BELOW
    standing = None if simple else await rankings.standing(session, user.id, city.id, period)
    card = NotchCard(
        card_code=card_code(period.start, card_no),
        card_no=card_no,
        user_id=user.id,
        city_id=city.id,
        period=period.start,
        name=user.name,
        notch_count=stats.notch_count,
        places_count=stats.places_count,
        top_venue_id=stats.top_venue_id,
        top_venue_name=stats.top_venue_name,
        top_venue_visits=stats.top_venue_visits,
        regular_since=stats.regular_since,
        vibe=stats.vibe,
        avg_time=stats.avg_time,
        city_rank=standing.rank if standing else None,
        rank_percent=standing.top_percent if standing else None,
        active_users=standing.active_users if standing else 0,
        unnotched_count=await unnotched_count(session, user.id, city.id),
        is_simple=simple,
    )
    card.face = best_face(card)
    return card
