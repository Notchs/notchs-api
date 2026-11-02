import uuid
from datetime import datetime

from app.common.schemas import BaseSchema, PublicCode
from app.modules.venues.counter_schemas import CounterOut
from app.modules.venues.models import VenueCategory, VenueStatus
from app.modules.venues.schemas import VenueOut


class AdminVenueRowOut(BaseSchema):
    id: uuid.UUID
    venue_code: PublicCode
    name: str
    area: str | None
    category: VenueCategory
    status: VenueStatus
    founding_venue_no: int | None
    notches_7d: int
    counter_online: bool | None  # None without a counter
    created_at: datetime


class AdminVenueOut(VenueOut):
    area: str | None
    city: str
    notches_7d: int
    notches_total: int
    counter: CounterOut | None
