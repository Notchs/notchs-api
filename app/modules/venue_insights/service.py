"""A venue's own numbers for its dashboard, computed from real visits and scans only.

    overview   visits and new guests per day for the range, against the range before it,
               returning rate, the counter phone, and the latest scans
    scans      every scan attempt in the range and why each was or wasn't a Notch
    guests     each guest as "Guest #N" with their visit pattern, never who they are

Days and hours are India time. At MVP size (hundreds of visits per venue) the visits are read
once and counted here; move the counting into SQL if a venue gets much bigger.
"""

import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.time import IST, ist_date
from app.modules.scans.models import ScanStatus
from app.modules.venue_insights.repository import InsightsRepository
from app.modules.venue_insights.schemas import (
    Activity,
    CounterSnapshot,
    DayCount,
    FirstDays,
    Funnel,
    Guest,
    GuestsOut,
    GuestsSummary,
    OverviewOut,
    Range,
    ScanRecord,
    ScansOut,
    Segment,
    Stage,
)
from app.modules.venues.counters import ONLINE_WITHIN

# The dashboard shows its "first days" checklist until this many verified visits.
FIRST_DAYS_TARGET = 10
RECENT_SCANS = 5
MAX_SCANS = 500
MAX_GUESTS = 500
REGULAR_FROM = 5
AT_RISK_AFTER = timedelta(days=30)
MONTHS_SHOWN = 6


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(), IST)


def _returning_rate(visits: Sequence[Row[Any]]) -> int | None:
    guests = {v.user_id for v in visits}
    if not guests:
        return None
    returning = {v.user_id for v in visits if v.visit_no_at_venue > 1}
    return round(100 * len(returning) / len(guests))


def _per_day(visits: Sequence[Row[Any]], start: date, days: int) -> list[DayCount]:
    """Visits and first-time guests for each IST day from `start`. A visit outside those days
    (after `now`, from clock skew) is left out."""
    counts: dict[date, list[int]] = {start + timedelta(days=n): [0, 0] for n in range(days)}
    for row in visits:
        day = counts.get(ist_date(row.visited_at))
        if day is None:
            continue
        day[0] += 1
        day[1] += row.visit_no_at_venue == 1
    return [DayCount(day=d, visits=v, new_guests=n) for d, (v, n) in counts.items()]


def _months_back(today: date, count: int) -> list[str]:
    year, month = today.year, today.month
    labels = []
    for _ in range(count):
        labels.append(f"{year:04d}-{month:02d}")
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return labels[::-1]


def segment_of(total: int, last_visit_at: datetime, now: datetime) -> Segment:
    if total >= 2 and now - last_visit_at > AT_RISK_AFTER:
        return Segment.AT_RISK
    if total >= REGULAR_FROM:
        return Segment.REGULAR
    return Segment.RETURNING if total >= 2 else Segment.NEW


class InsightsService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = InsightsRepository(session)

    async def overview(
        self, venue_id: uuid.UUID, range_: Range, now: datetime | None = None
    ) -> OverviewOut:
        now = now or datetime.now(UTC)
        today = ist_date(now)
        start = today - timedelta(days=range_.days - 1)
        previous_start = start - timedelta(days=range_.days)
        rows = await self.repository.visits(venue_id, since=_midnight(previous_start))
        current = [r for r in rows if ist_date(r.visited_at) >= start]
        previous = [r for r in rows if ist_date(r.visited_at) < start]

        activity = Activity(
            days=_per_day(current, start, range_.days),
            previous_days=_per_day(previous, previous_start, range_.days),
            visits=len(current),
            visits_previous=len(previous),
            new_guests=sum(r.visit_no_at_venue == 1 for r in current),
            new_guests_previous=sum(r.visit_no_at_venue == 1 for r in previous),
            unique_guests=len({r.user_id for r in current}),
            returning_rate=_returning_rate(current),
            returning_rate_previous=_returning_rate(previous),
        )

        verified = await self.repository.count_visits(venue_id)
        counter = await self.repository.counter(venue_id)
        paired = counter is not None and counter.token_hash is not None
        last_seen = counter.last_seen_at if counter else None
        numbers = await self.repository.guest_numbers(venue_id)
        recent = await self.repository.scans(venue_id, _midnight(start), limit=RECENT_SCANS)
        return OverviewOut(
            as_of=now,
            range=range_,
            stage=Stage.LIVE if verified >= FIRST_DAYS_TARGET else Stage.FIRST_DAYS,
            activity=activity,
            counter=CounterSnapshot(
                paired=paired,
                online=paired and last_seen is not None and now - last_seen <= ONLINE_WITHIN,
                last_seen_at=last_seen,
                last_verified_visit_at=await self.repository.last_visit_at(venue_id),
            ),
            first_days=FirstDays(verified_visits=verified, target=FIRST_DAYS_TARGET),
            recent_scans=[_scan_record(row, numbers) for row in recent],
        )

    async def scans(
        self, venue_id: uuid.UUID, range_: Range, now: datetime | None = None
    ) -> ScansOut:
        now = now or datetime.now(UTC)
        start = ist_date(now) - timedelta(days=range_.days - 1)
        rows = await self.repository.scans(venue_id, _midnight(start))
        refused = Counter(r.reject_reason for r in rows if r.status != ScanStatus.VERIFIED)
        numbers = await self.repository.guest_numbers(venue_id)
        return ScansOut(
            range=range_,
            funnel=Funnel(
                attempts=len(rows),
                verified=len(rows) - sum(refused.values()),
                refused=dict(refused),
            ),
            scans=[_scan_record(row, numbers) for row in rows[:MAX_SCANS]],
        )

    async def guests(self, venue_id: uuid.UUID, now: datetime | None = None) -> GuestsOut:
        now = now or datetime.now(UTC)
        months = _months_back(ist_date(now), MONTHS_SHOWN)
        visits_of: dict[uuid.UUID, list[datetime]] = defaultdict(list)
        for row in await self.repository.visits(venue_id):
            visits_of[row.user_id].append(row.visited_at)
        numbers = await self.repository.guest_numbers(venue_id)

        guests = []
        for user_id, times in visits_of.items():
            local = [t.astimezone(IST) for t in times]
            per_month = Counter(t.strftime("%Y-%m") for t in local)
            span_days = (times[-1] - times[0]).total_seconds() / 86400
            guests.append(
                (
                    user_id,
                    Guest(
                        guest_no=numbers[user_id],
                        segment=segment_of(len(times), times[-1], now),
                        total_visits=len(times),
                        first_visit_at=times[0],
                        last_visit_at=times[-1],
                        average_gap_days=(
                            round(span_days / (len(times) - 1), 1) if len(times) > 1 else None
                        ),
                        days_to_second_visit=(
                            round((times[1] - times[0]).total_seconds() / 86400, 1)
                            if len(times) > 1
                            else None
                        ),
                        visits_by_month=[per_month[m] for m in months],
                        usual_hour=Counter(t.hour for t in local).most_common(1)[0][0],
                        instagram_handle=None,
                    ),
                )
            )
        guests.sort(key=lambda pair: pair[1].last_visit_at, reverse=True)
        shown = guests[:MAX_GUESTS]
        instagram = await self.repository.shared_instagram([user_id for user_id, _ in shown])
        for user_id, guest in shown:
            guest.instagram_handle = instagram.get(user_id)

        segments = Counter(guest.segment for _, guest in guests)
        return GuestsOut(
            as_of=now,
            summary=GuestsSummary(
                guests=len(guests),
                new=segments[Segment.NEW],
                returning=segments[Segment.RETURNING],
                regulars=segments[Segment.REGULAR],
                at_risk=segments[Segment.AT_RISK],
            ),
            month_labels=months,
            guests=[guest for _, guest in shown],
        )


def _scan_record(row: Row[Any], numbers: dict[uuid.UUID, int]) -> ScanRecord:
    return ScanRecord(
        received_at=row.created_at,
        outcome=ScanStatus.VERIFIED if row.status == ScanStatus.VERIFIED else row.reject_reason,
        notch_no=row.notch_no,
        guest_no=numbers.get(row.user_id),
        code=row.code_submitted,
        counter_name=row.counter_name,
    )
