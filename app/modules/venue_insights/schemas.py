"""What a venue sees about its own visits. Guests are numbers ("Guest #10"), never a person: no
name, phone number or handle. The one exception is an Instagram username the customer chose to
show to venues (team decision, Q35)."""

from datetime import date, datetime
from enum import StrEnum

from app.common.schemas import BaseSchema


class Range(StrEnum):
    WEEK = "7d"
    MONTH = "30d"

    @property
    def days(self) -> int:
        return 7 if self is Range.WEEK else 30


class Stage(StrEnum):
    FIRST_DAYS = "first_days"  # fewer than FIRST_DAYS_TARGET verified visits ever
    LIVE = "live"


class Segment(StrEnum):
    NEW = "new"  # one visit
    RETURNING = "returning"  # 2 to 4 visits
    REGULAR = "regular"  # 5 or more visits
    AT_RISK = "at_risk"  # came back before, but not in the last 30 days


class DayCount(BaseSchema):
    day: date
    visits: int
    new_guests: int


class Activity(BaseSchema):
    days: list[DayCount]
    visits: int
    visits_previous: int
    new_guests: int
    new_guests_previous: int
    unique_guests: int
    # Percent of this range's guests who had been here before; None with no guests.
    returning_rate: int | None
    returning_rate_previous: int | None


class CounterSnapshot(BaseSchema):
    paired: bool
    online: bool
    last_seen_at: datetime | None
    last_verified_visit_at: datetime | None


class FirstDays(BaseSchema):
    verified_visits: int
    target: int


class ScanRecord(BaseSchema):
    received_at: datetime
    # verified, or why it was refused (scans.RejectReason)
    outcome: str
    notch_no: int | None
    # None when the person has never been notched here.
    guest_no: int | None
    code: str | None
    counter_name: str | None


class OverviewOut(BaseSchema):
    as_of: datetime
    range: Range
    stage: Stage
    activity: Activity
    counter: CounterSnapshot
    first_days: FirstDays
    recent_scans: list[ScanRecord]


class Funnel(BaseSchema):
    attempts: int
    verified: int
    # Refusals by reason (scans.RejectReason), e.g. {"already_today": 3}
    refused: dict[str, int]


class ScansOut(BaseSchema):
    range: Range
    funnel: Funnel
    scans: list[ScanRecord]


class Guest(BaseSchema):
    guest_no: int
    segment: Segment
    total_visits: int
    first_visit_at: datetime
    last_visit_at: datetime
    # Average days between visits; None after one visit.
    average_gap_days: float | None
    # Oldest first, matching `month_labels`.
    visits_by_month: list[int]
    # The hour (IST, 0-23) they come most often.
    usual_hour: int
    instagram_handle: str | None


class GuestsSummary(BaseSchema):
    guests: int
    new: int
    returning: int
    regulars: int
    at_risk: int


class GuestsOut(BaseSchema):
    as_of: datetime
    summary: GuestsSummary
    month_labels: list[str]
    guests: list[Guest]
