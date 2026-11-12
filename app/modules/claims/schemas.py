"""Claims as the customer and the admin see them. The admin sees the customer's handle, never
their phone number."""

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from app.common.schemas import BaseSchema, PublicCode
from app.modules.claims.models import ClaimRejectCode

# What the customer sees: a claim still waiting for its photo after its day is "expired".
CustomerClaimStatus = Literal["awaiting_photo", "expired", "in_review", "approved", "rejected"]


class ClaimVenueOut(BaseSchema):
    venue_code: str
    name: str
    area: str | None
    niche: str


class ClaimStartIn(BaseSchema):
    venue_code: PublicCode


class ClaimStartOut(BaseSchema):
    claim_code: str
    venue_name: str
    # What to send first: "CLAIM CLM-7K3M9QX2". Then the bill photo.
    message: str
    # None until WHATSAPP_PUBLIC_NUMBER is set.
    whatsapp_link: str | None
    # The photo must arrive today (India time).
    expires_at: datetime


class MyClaimOut(BaseSchema):
    claim_code: str
    venue_name: str
    visit_date: date
    status: CustomerClaimStatus
    notch_no: int | None
    reject_reason: str | None
    created_at: datetime


class AdminClaimOut(BaseSchema):
    claim_code: str
    status: str
    venue_name: str
    venue_code: str
    niche: str
    customer_handle: str
    visit_date: date
    created_at: datetime
    photo_received_at: datetime | None
    # WhatsApp keeps photos about 30 days.
    photo_available: bool
    # When the place's counter phone last checked in, to see if it really was down.
    counter_last_seen_at: datetime | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    reject_reason: str | None
    notch_no: int | None


class ClaimRejectIn(BaseSchema):
    # Sent to the customer.
    reason: str = Field(min_length=3, max_length=200)
    # For counting why claims fail. Optional for now so the admin panel keeps working.
    code: ClaimRejectCode = ClaimRejectCode.OTHER


class ClaimDecisionOut(BaseSchema):
    claim: AdminClaimOut
    # Whether the customer got a WhatsApp message (only inside 24 hours of their last message).
    customer_notified: bool
