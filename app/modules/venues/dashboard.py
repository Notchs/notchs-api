"""The venue owner's numbers (E5-E14), from counted Notchs at their venue.

customers   different customers in the range (anonymous visits of customers who deleted
            their data count one each)
new         customers whose first ever visit here was in the range (`user_visit_no` = 1)
regulars    customers with 5+ visits here this calendar month
"""

import csv
import io
import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.email import EmailMessage
from app.common.dependencies import PaginationParams
from app.core.config import settings
from app.modules.audit.models import ActorType, AuditLog
from app.modules.cards.models import VenueCard
from app.modules.locations.models import Area, City
from app.modules.locations.time import Period, last_days, local, month_label, month_of, today
from app.modules.notches.badges import REGULAR_VISITS
from app.modules.notches.models import COUNTED_STATUSES, Notch
from app.modules.venues.counters import counter_out
from app.modules.venues.dashboard_schemas import (
    BarOut,
    BillingOut,
    BusiestOut,
    CertificateOut,
    ContactOut,
    ContactUpdate,
    CustomersOut,
    DayOut,
    OwnerHomeOut,
    Range,
    VenueCardOut,
    VisitOut,
)
from app.modules.venues.exceptions import (
    CardNotReadyException,
    NotFoundingVenueException,
    RangeInvalidException,
)
from app.modules.venues.models import (
    MAX_FOUNDING_VENUE_NO,
    CounterDevice,
    Venue,
    VenueMember,
)
from app.modules.venues.page import venue_page
from app.modules.venues.schemas import VenuePageOut, VenueProfileUpdate

UNLOCK_AT_VISITS = 10
GST_PERCENT = 18
MAX_DAILY_RANGE_DAYS = 92
MAX_EXPORT_DAYS = 366
TODAY_VISITS_SHOWN = 20
_DAY_LETTERS = "MTWTFSS"
EN_DASH = "\u2013"  # the design writes hour ranges with an en dash
_COUNTED = Notch.status.in_(COUNTED_STATUSES)


def ordinal(n: int) -> str:
    """1st, 2nd, 3rd, 4th ... 11th, 12th, 13th ... 21st."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _hour_label(hour: int) -> tuple[int, str]:
    return (hour % 12 or 12), ("AM" if hour % 24 < 12 else "PM")


def busiest_label(start_hour: int) -> str:
    """Two hours from `start_hour`: 18 -> "6-8 PM", 11 -> "11 AM-1 PM" (with an en dash)."""
    start, start_half = _hour_label(start_hour)
    end, end_half = _hour_label((start_hour + 2) % 24)
    if start_half == end_half:
        return f"{start}{EN_DASH}{end} {end_half}"
    return f"{start} {start_half}{EN_DASH}{end} {end_half}"


def busiest_window(hours: dict[int, int]) -> int | None:
    """The start of the busiest two-hour window, over the 24-hour circle."""
    if not hours:
        return None
    return max(range(24), key=lambda h: (hours.get(h, 0) + hours.get((h + 1) % 24, 0), -h))


def bars_for(period: Period, per_day: dict[date, int]) -> list[BarOut]:
    """One bar per day for 7 days; 7-day weeks (the first may be shorter) for longer ranges."""
    if period.days <= 7:
        days = [period.start + timedelta(days=i) for i in range(period.days)]
        return [
            BarOut(label=_DAY_LETTERS[d.weekday()], start=d, end=d, notches=per_day.get(d, 0))
            for d in days
        ]
    bars = []
    end = period.end
    while end >= period.start:
        start = max(period.start, end - timedelta(days=6))
        total = sum(n for d, n in per_day.items() if start <= d <= end)
        bars.append((start, end, total))
        end = start - timedelta(days=1)
    bars.reverse()
    return [BarOut(label=f"W{i}", start=s, end=e, notches=n) for i, (s, e, n) in enumerate(bars, 1)]


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _venue(self, member: VenueMember) -> tuple[Venue, City]:
        venue = await self.session.get(Venue, member.venue_id)
        assert venue is not None  # the member dependency checks the venue exists
        city = await self.session.get(City, venue.city_id)
        assert city is not None
        return venue, city

    def _at(self, venue_id: uuid.UUID, period: Period | None = None) -> list[Any]:
        conditions = [Notch.venue_id == venue_id, _COUNTED]
        if period is not None:
            conditions.append(Notch.local_date.between(period.start, period.end))
        return conditions

    async def _customers(self, conditions: list[Any]) -> tuple[int, int]:
        """(different customers, new customers) for these Notchs."""
        row = (
            await self.session.execute(
                select(
                    func.count(func.distinct(Notch.user_id)),
                    func.count().filter(Notch.user_id.is_(None)),
                    func.count().filter(Notch.user_visit_no == 1),
                ).where(*conditions)
            )
        ).one()
        return row[0] + row[1], row[2]

    @staticmethod
    def _range(range_: Range, day: date) -> Period:
        return last_days(day, 7 if range_ == "7d" else 30)

    # --- home (E5) -----------------------------------------------------------------------

    async def home(self, member: VenueMember, range_: Range) -> OwnerHomeOut:
        venue, city = await self._venue(member)
        period = self._range(range_, today(city.timezone))
        conditions = self._at(venue.id, period)
        customers, new = await self._customers(conditions)
        per_day = dict(
            (
                await self.session.execute(
                    select(Notch.local_date, func.count())
                    .where(*conditions)
                    .group_by(Notch.local_date)
                )
            ).all()
        )
        hours = dict(
            (
                await self.session.execute(
                    select(Notch.local_hour, func.count())
                    .where(*conditions)
                    .group_by(Notch.local_hour)
                )
            ).all()
        )
        start_hour = busiest_window(hours)
        total = await self.session.scalar(select(func.count()).where(*self._at(venue.id))) or 0
        counter = await self.session.scalar(
            select(CounterDevice).where(
                CounterDevice.venue_id == venue.id, CounterDevice.revoked_at.is_(None)
            )
        )
        span = "this week" if range_ == "7d" else "this month"
        noun = "customer" if customers == 1 else "customers"
        return OwnerHomeOut(
            venue_name=venue.name,
            range=range_,
            unlocked=total >= UNLOCK_AT_VISITS,
            visits_so_far=total,
            unlock_at=UNLOCK_AT_VISITS,
            customers=customers,
            new_customers=new,
            headline=f"{customers} {noun} {span} · {new} new",
            bars=bars_for(period, per_day),
            busiest=(
                BusiestOut(
                    start_hour=start_hour,
                    end_hour=(start_hour + 2) % 24,
                    label=busiest_label(start_hour),
                )
                if start_hour is not None
                else None
            ),
            counter=counter_out(counter) if counter else None,
        )

    async def daily(self, member: VenueMember, start: date, end: date) -> list[DayOut]:
        """The daily breakdown behind a number (E5 Layer 2, "compare months")."""
        if end < start or (end - start).days >= MAX_DAILY_RANGE_DAYS:
            raise RangeInvalidException(
                f"Pick up to {MAX_DAILY_RANGE_DAYS} days, the start before the end."
            )
        venue, _ = await self._venue(member)
        rows = await self.session.execute(
            select(
                Notch.local_date,
                func.count(),
                func.count(func.distinct(Notch.user_id))
                + func.count().filter(Notch.user_id.is_(None)),
                func.count().filter(Notch.user_visit_no == 1),
            )
            .where(*self._at(venue.id, Period(start, end)))
            .group_by(Notch.local_date)
        )
        found = {d: DayOut(date=d, notches=n, customers=c, new_customers=k) for d, n, c, k in rows}
        return [
            found.get(
                start + timedelta(days=i),
                DayOut(date=start + timedelta(days=i), notches=0, customers=0, new_customers=0),
            )
            for i in range((end - start).days + 1)
        ]

    # --- customers (E6) ------------------------------------------------------------------

    async def customers(self, member: VenueMember, range_: Range) -> CustomersOut:
        venue, city = await self._venue(member)
        day = today(city.timezone)
        customers, new = await self._customers(self._at(venue.id, self._range(range_, day)))
        per_customer = (
            select(func.count().label("visits"))
            .where(*self._at(venue.id, month_of(day)), Notch.user_id.is_not(None))
            .group_by(Notch.user_id)
            .subquery()
        )
        regulars = await self.session.scalar(
            select(func.count())
            .select_from(per_customer)
            .where(per_customer.c.visits >= REGULAR_VISITS)
        )
        today_notches = await self.session.scalars(
            select(Notch)
            .where(*self._at(venue.id, Period(day, day)))
            .order_by(Notch.scanned_at.desc())
            .limit(TODAY_VISITS_SHOWN)
        )
        return CustomersOut(
            range=range_,
            new=new,
            returning=customers - new,
            regulars=regulars or 0,
            today=[self._visit(n, city.timezone) for n in today_notches],
        )

    @staticmethod
    def _visit(notch: Notch, tz: str | None) -> VisitOut:
        return VisitOut(
            scanned_at=notch.scanned_at,
            time=local(notch.scanned_at, tz).strftime("%H:%M"),
            notch_no=notch.notch_no,
            visit_no=notch.user_visit_no,
            label=f"{ordinal(notch.user_visit_no)} visit",
        )

    async def visits(
        self, member: VenueMember, day: date | None, pagination: PaginationParams
    ) -> tuple[list[VisitOut], int]:
        venue, city = await self._venue(member)
        day = day or today(city.timezone)
        conditions = self._at(venue.id, Period(day, day))
        rows = await self.session.scalars(
            select(Notch)
            .where(*conditions)
            .order_by(Notch.scanned_at.desc())
            .offset(pagination.offset)
            .limit(pagination.size)
        )
        total = await self.session.scalar(select(func.count()).where(*conditions)) or 0
        return [self._visit(n, city.timezone) for n in rows], total

    async def export_csv(self, member: VenueMember, start: date, end: date) -> str:
        """Every counted visit in the range, one per row. No customer details (E6 Layer 3)."""
        if end < start or (end - start).days >= MAX_EXPORT_DAYS:
            raise RangeInvalidException(f"Export up to {MAX_EXPORT_DAYS} days at a time.")
        venue, city = await self._venue(member)
        rows = await self.session.scalars(
            select(Notch).where(*self._at(venue.id, Period(start, end))).order_by(Notch.scanned_at)
        )
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["date", "time", "notch_no", "visit_no", "new_customer"])
        for notch in rows:
            writer.writerow(
                [
                    notch.local_date.isoformat(),
                    local(notch.scanned_at, city.timezone).strftime("%H:%M"),
                    notch.notch_no,
                    notch.user_visit_no,
                    "yes" if notch.user_visit_no == 1 else "no",
                ]
            )
        return out.getvalue()

    # --- profile (E8), contact (E11) -----------------------------------------------------

    async def profile(self, member: VenueMember) -> VenuePageOut:
        venue, _ = await self._venue(member)
        return await venue_page(self.session, venue)

    async def update_profile(self, member: VenueMember, body: VenueProfileUpdate) -> VenuePageOut:
        venue, _ = await self._venue(member)
        for field in body.model_fields_set:
            value = getattr(body, field)
            setattr(venue, field, [] if field == "tags" and value is None else value)
        await self.session.flush()
        await self.session.refresh(venue)
        return await venue_page(self.session, venue)

    async def update_contact(self, member: VenueMember, body: ContactUpdate) -> ContactOut:
        if body.name is not None:
            member.name = body.name
        if body.phone is not None:
            member.phone_e164 = body.phone
        await self.session.flush()
        return ContactOut(name=member.name, phone=member.phone_e164, email=member.email)

    # --- cards (E7, E14) and billing (E10) -----------------------------------------------

    async def cards(self, member: VenueMember) -> list[VenueCardOut]:
        venue, city = await self._venue(member)
        rows = await self.session.scalars(
            select(VenueCard)
            .where(VenueCard.venue_id == venue.id)
            .order_by(VenueCard.period.desc())
        )
        area = await self._area_name(venue)
        return [self._card(card, venue, city, area) for card in rows]

    async def card(self, member: VenueMember, month: Period) -> VenueCardOut:
        venue, city = await self._venue(member)
        card = await self.session.scalar(
            select(VenueCard).where(VenueCard.venue_id == venue.id, VenueCard.period == month.start)
        )
        if card is None:
            raise CardNotReadyException()
        return self._card(card, venue, city, await self._area_name(venue))

    @staticmethod
    def _card(card: VenueCard, venue: Venue, city: City, area: str | None) -> VenueCardOut:
        label = month_label(card.period)
        return VenueCardOut(
            month=card.period,
            month_label=label,
            headline=f"{venue.name}'s {label}",
            venue_name=venue.name,
            area=area,
            city=city.name,
            visitors=card.visitors,
            new_visitors=card.new_visitors,
            new_percent=round(card.new_visitors / card.visitors * 100) if card.visitors else 0,
            notches=card.notches,
        )

    async def certificate(self, member: VenueMember) -> CertificateOut:
        venue, city = await self._venue(member)
        if venue.founding_venue_no is None:
            raise NotFoundingVenueException()
        return CertificateOut(
            founding_venue_no=venue.founding_venue_no,
            total=MAX_FOUNDING_VENUE_NO,
            venue_name=venue.name,
            area=await self._area_name(venue),
            city=city.name,
            joined_on=local(venue.created_at, city.timezone).date(),
        )

    async def billing(self, member: VenueMember) -> BillingOut:
        venue, city = await self._venue(member)
        day = today(city.timezone)
        cycle = month_of(day)
        visits = await self.session.scalar(select(func.count()).where(*self._at(venue.id, cycle)))
        return BillingOut(
            free_until=venue.free_until,
            on_free_months=venue.free_until is not None and day <= venue.free_until,
            fee_paise=(
                venue.monthly_fee_paise
                if venue.monthly_fee_paise is not None
                else settings.DEFAULT_VENUE_FEE_PAISE
            ),
            gst_percent=GST_PERCENT,
            cycle_start=cycle.start,
            cycle_end=cycle.end,
            verified_visits_this_cycle=visits or 0,
        )

    async def close_request(self, member: VenueMember, reason: str) -> EmailMessage | None:
        """E11's "Delete venue" asks the Notchs team: billing and history need a person."""
        venue, _ = await self._venue(member)
        self.session.add(
            AuditLog(
                actor_type=ActorType.VENUE_MEMBER,
                actor_id=member.id,
                actor_label=member.name,
                action="venue.close_requested",
                target_type="venue",
                target_id=venue.id,
                target_label=venue.name,
                details={"reason": reason} if reason else {},
            )
        )
        await self.session.flush()
        if not settings.ADMIN_NOTIFY_EMAIL:
            return None
        return EmailMessage(
            to=settings.ADMIN_NOTIFY_EMAIL,
            subject=f"Close request: {venue.name}",
            text=(
                f"{venue.name} ({venue.venue_code}) asked to close their Notchs venue.\n\n"
                f"Reason: {reason or '(none given)'}\n\n"
                f"Handle it in the admin panel: {settings.ADMIN_APP_URL}/venues/{venue.venue_code}"
            ),
        )

    async def _area_name(self, venue: Venue) -> str | None:
        if venue.area_id is None:
            return None
        return await self.session.scalar(select(Area.name).where(Area.id == venue.area_id))
