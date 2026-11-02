"""The public website, notchs.com (A1-A15). No login; only real numbers."""

import uuid
from datetime import date
from typing import Literal

from pydantic import Field

from app.common.phone import Phone
from app.common.schemas import BaseSchema
from app.modules.venues.schemas import VenueBriefOut

RankingSort = Literal["most_visited", "trending"]


class JoinIn(BaseSchema):
    """The Founding 500 form (A1 stop 5, A14): name, WhatsApp number, area, the checkbox."""

    name: str = Field(min_length=1, max_length=60)
    phone: Phone = Field(examples=["98765 43210"])
    area_id: uuid.UUID | None = None
    # "I'm 18+ and I agree to the privacy notice." Must be ticked.
    agree: Literal[True]
    turnstile_token: str = Field(default="", max_length=2048)


class JoinOut(BaseSchema):
    # Set only when this sign-up got a new number. A number already on the list gets the same
    # answer without one.
    founding_member_no: int | None
    founding_members_left: int


class StatsOut(BaseSchema):
    """Real counters only: "358 left", "23 of 30 left"."""

    founding_members_left: int
    founding_members_total: int
    founding_venues_left: int
    founding_venues_total: int


class CategoryTileOut(BaseSchema):
    slug: str  # the A7 page: /ahmedabad/cafes
    label: str
    venues_live: int


class TrendingOut(BaseSchema):
    venue: VenueBriefOut
    visits_this_week: int


class AreaLinkOut(BaseSchema):
    name: str
    slug: str
    venues_live: int


class CityStatsOut(BaseSchema):
    month_label: str
    notches_this_month: int
    explorers_this_month: int
    venues_live: int


class CityPageOut(BaseSchema):
    """The city page (A6): "Ahmedabad's real-life leaderboard"."""

    name: str
    slug: str
    # False until the city has a full week of Notchs: "Rankings start after the first week".
    has_rankings: bool
    categories: list[CategoryTileOut]
    trending: list[TrendingOut]
    areas: list[AreaLinkOut]
    stats: CityStatsOut


class RankingRowOut(BaseSchema):
    rank: int
    venue: VenueBriefOut
    visits: int  # verified visits that week
    previous_visits: int  # the week before, for "trending"


class RankingOut(BaseSchema):
    """A category ranking (A7): "Best cafés in Ahmedabad, ranked by real visits"."""

    city: str
    city_slug: str
    category: str  # "cafes"
    category_label: str  # "Cafés"
    sort: RankingSort
    week_start: date
    week_end: date
    updated_on: date  # the Monday after the week: "Updated every Monday"
    area: AreaLinkOut | None
    items: list[RankingRowOut]
    # Fewer than 5 places: show "More cafés join every week."
    few_places: bool
    past_weeks: list[date]  # earlier week starts, newest first


class PulseMonthOut(BaseSchema):
    month: date
    month_label: str
    visits_verified: int
    fakes_blocked: int


class PulseOut(BaseSchema):
    """Notchs Pulse (A4): every finished month, real numbers only."""

    months: list[PulseMonthOut]
    # Before the first full month: "First Pulse: 1 Dec 2026."
    first_pulse_on: date | None
