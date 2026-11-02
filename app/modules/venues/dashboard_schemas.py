"""The venue app's screens (E5-E14). Totals only: never a customer's phone number or name."""

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from app.common.phone import Phone
from app.common.schemas import BaseSchema
from app.modules.venues.counter_schemas import CounterOut

Range = Literal["7d", "30d"]


class BarOut(BaseSchema):
    label: str  # "M", "T"... for 7 days; "W1".."W5" for 30 days
    start: date
    end: date
    notches: int


class BusiestOut(BaseSchema):
    start_hour: int
    end_hour: int
    label: str  # "6-8 PM", with an en dash


class OwnerHomeOut(BaseSchema):
    """Owner home (E5): "Is Notchs bringing me customers?", answered in 5 seconds."""

    venue_name: str
    range: Range
    # The full numbers open after the first 10 visits; before that, `visits_so_far` and the
    # counter phone's state carry the screen ("Your first customers are on their way").
    unlocked: bool
    visits_so_far: int
    unlock_at: int
    customers: int
    new_customers: int
    headline: str  # "86 customers this week · 31 new"
    bars: list[BarOut]
    busiest: BusiestOut | None
    counter: CounterOut | None


class DayOut(BaseSchema):
    date: date
    notches: int
    customers: int
    new_customers: int


class VisitOut(BaseSchema):
    scanned_at: datetime
    time: str  # "18:12"
    notch_no: int
    visit_no: int
    label: str  # "1st visit", "6th visit"


class CustomersOut(BaseSchema):
    """New vs returning, and regulars (E6)."""

    range: Range
    new: int
    returning: int
    # Customers with 5+ visits this calendar month.
    regulars: int
    today: list[VisitOut]


class ContactUpdate(BaseSchema):
    """The owner's own details (E11 "Owner contact"). The login email changes elsewhere."""

    name: str | None = Field(None, min_length=1, max_length=60)
    phone: Phone | None = None


class ContactOut(BaseSchema):
    name: str
    phone: str
    email: str


class VenueCardOut(BaseSchema):
    """The venue's monthly card (E7): "Kahwa House's October: 312 visitors, 41% new"."""

    month: date
    month_label: str
    headline: str  # "Kahwa House's October"
    venue_name: str
    area: str | None
    city: str
    visitors: int
    new_visitors: int
    new_percent: int
    notches: int


class CertificateOut(BaseSchema):
    """The Founding Venue certificate (E14): "Founding Venue #7 of 30"."""

    founding_venue_no: int
    total: int
    venue_name: str
    area: str | None
    city: str
    joined_on: date


class BillingOut(BaseSchema):
    """Plan and usage (E10). Payments are not connected yet: no invoices, no AutoPay."""

    free_until: date | None
    on_free_months: bool
    # After the free months, per month; None until the price is set.
    fee_paise: int | None
    gst_percent: int
    cycle_start: date
    cycle_end: date
    verified_visits_this_cycle: int
    invoices: list[dict[str, str]] = Field(default_factory=list)
    payments_available: bool = False


class CloseRequestIn(BaseSchema):
    reason: str = Field(default="", max_length=500)
