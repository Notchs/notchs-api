"""The admin panel's views. Customers appear by handle with only the last 4 digits of their
phone number: admin lists never show full numbers (CLAUDE.md, security rules)."""

from datetime import date, datetime
from enum import StrEnum

from pydantic import Field

from app.common.schemas import BaseSchema
from app.modules.users.models import UserStatus


class OverviewOut(BaseSchema):
    as_of: datetime
    venues_live: int
    venues_signed: int
    venues_paused: int
    applications_pending: int
    customers: int
    customers_new_7d: int
    notchs_today: int
    notchs_7d: int
    scans_refused_today: int


class AdminVenueRow(BaseSchema):
    venue_code: str
    name: str
    category: str
    area: str | None
    status: str
    joined_at: datetime
    counter_paired: bool
    counter_online: bool
    counter_last_seen_at: datetime | None
    notchs_total: int
    notchs_7d: int


class VenueStatusTarget(StrEnum):
    LIVE = "counter_live"
    PAUSED = "paused"
    CLOSED = "closed"


class VenueStatusIn(BaseSchema):
    status: VenueStatusTarget


class AdminNotchRow(BaseSchema):
    venue_code: str
    venue_name: str
    notch_no: int
    customer_handle: str | None
    visit_date: date
    visited_at: datetime
    visit_no_at_venue: int
    status: str
    points: int


class RevokeIn(BaseSchema):
    reason: str = Field(min_length=3, max_length=200)


class AdminCustomerRow(BaseSchema):
    handle: str
    name: str | None
    # Last 4 digits only, e.g. "…3210".
    phone_last4: str
    city: str | None
    status: str
    founding_member_no: int | None
    notchs: int
    joined_at: datetime
    last_seen_at: datetime | None


class CustomerStatusTarget(StrEnum):
    ACTIVE = UserStatus.ACTIVE.value
    BLOCKED = UserStatus.BLOCKED.value


class CustomerStatusIn(BaseSchema):
    status: CustomerStatusTarget
