"""The customer's passport (C1-C14). Never returns anyone's phone number but the customer's
own, and that only in the data download."""

import uuid
from datetime import date, datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from app.common.schemas import BaseSchema, PublicCode
from app.modules.notches.models import Badge
from app.modules.scans.models import VisitStatus
from app.modules.users.models import UserTheme
from app.modules.venues.schemas import VenueBriefOut, VenuePageOut

Period = Literal["week", "month", "all"]


class AreaOut(BaseSchema):
    id: uuid.UUID
    name: str


class CityOut(BaseSchema):
    id: uuid.UUID
    name: str
    slug: str


class FirstNotchOut(BaseSchema):
    """The claim screen's "Your first Notch: Kahwa House" (C1)."""

    venue: VenueBriefOut
    scanned_at: datetime
    time: str  # "18:12", in the city's time


class MeOut(BaseSchema):
    id: uuid.UUID
    user_code: PublicCode
    name: str | None
    # True until the customer has given a name: show the claim screen (C1).
    needs_name: bool
    area: AreaOut | None
    city: CityOut | None
    founding_member_no: int | None
    founding_member_total: int
    theme: UserTheme
    is_public: bool
    notches_total: int
    badges_count: int
    cards_count: int
    first_notch: FirstNotchOut | None
    joined_at: datetime


class MeUpdate(BaseSchema):
    """Claim (C1) and edit (C11, C12). Only the fields sent change."""

    name: str | None = Field(None, min_length=1, max_length=60)
    area_id: uuid.UUID | None = None
    theme: UserTheme | None = None
    is_public: bool | None = None

    @model_validator(mode="after")
    def _no_nulls(self) -> Self:
        for field in ("name", "theme", "is_public"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class RankOut(BaseSchema):
    position: int
    label: str  # "#38 in Ahmedabad"
    top_percent: int | None  # only with 200+ active customers


class NextStepOut(BaseSchema):
    """The ONE next step on Home: progress to Regular at a place."""

    venue: VenueBriefOut
    visits: int
    target: int
    left: int
    text: str  # "2 more visits to become the Regular here."


class UnnotchedOut(BaseSchema):
    venue: VenueBriefOut
    tag: str | None  # "Trending this week", "First Notch spots left"


class HomeOut(BaseSchema):
    first_name: str | None
    month: date
    month_label: str  # "October"
    notches_this_month: int
    # From the 3rd Notch of the month; until then `notches_to_rank` says how many more.
    rank: RankOut | None
    notches_to_rank: int | None
    next_step: NextStepOut | None
    unnotched: UnnotchedOut | None


class NotchOut(BaseSchema):
    notch_code: PublicCode
    venue: VenueBriefOut
    scanned_at: datetime
    local_date: date
    time: str  # "18:12"
    notch_no: int  # "Notch #0145"
    status: VisitStatus
    # Counted, but a reviewer is checking it.
    under_review: bool


class HistoryOut(BaseSchema):
    month: date
    month_label: str
    total: int
    items: list[NotchOut]  # newest first; group by local_date
    all_time_notches: int
    all_time_places: int


class ReportIn(BaseSchema):
    message: str = Field(min_length=3, max_length=500)


class ProgressOut(BaseSchema):
    current: int
    target: int


class BadgeOut(BaseSchema):
    badge: Badge
    name: str
    how: str
    earned: bool
    earned_at: datetime | None
    # Where it was earned, or where to earn it next.
    venue: VenueBriefOut | None
    detail: str | None  # "#142 of 500", "31 spots left at 4 places"
    progress: ProgressOut | None


class BadgesOut(BaseSchema):
    earned: list[BadgeOut]
    locked: list[BadgeOut]  # dimmed, not hidden: "3 more to discover"


class LeaderRowOut(BaseSchema):
    rank: int
    name: str  # "Riya S.", "Hidden explorer", or "You"
    area: str | None
    notches: int
    is_you: bool


class LeaderboardOut(BaseSchema):
    period: Period
    city: CityOut
    starts_on: date | None
    ends_on: date | None
    you: LeaderRowOut | None  # pinned on top
    top: list[LeaderRowOut]
    active_users: int
    top_percent: int | None


class PlaceOut(BaseSchema):
    venue: VenueBriefOut
    distance_m: int | None
    # One tag per place: "You're 2 from Regular", "First Notch spots left", "Trending".
    tag: str | None


class PlacesOut(BaseSchema):
    near_you: bool  # sorted by distance from the location sent
    area: AreaOut | None
    items: list[PlaceOut]
    areas: list[AreaOut]  # for "BY AREA" when location is not shared


class YourVisitsOut(BaseSchema):
    """The personal layer on the venue page (C6)."""

    visits_this_month: int
    visits_total: int
    left_to_regular: int
    is_regular_this_month: bool
    last_visit_on: date | None


class PassportVenueOut(VenuePageOut):
    you: YourVisitsOut


class TopPlaceOut(BaseSchema):
    name: str
    visits: int


class BusiestMonthOut(BaseSchema):
    month_label: str
    notches: int


class YearOut(BaseSchema):
    """The year-end recap, "Ahmedabad Notched 2026" (C14)."""

    year: int
    city: str
    name: str | None
    notches: int
    places: int
    place_of_the_year: TopPlaceOut | None
    busiest_month: BusiestMonthOut | None
    top_percent: int | None
    city_rank: int | None
    unnotched_count: int


class DeleteMeIn(BaseSchema):
    confirm: Literal[True]
