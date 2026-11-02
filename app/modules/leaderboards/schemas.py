"""Request and response bodies for leaderboards. Never a phone number."""

import uuid

from app.common.schemas import BaseSchema
from app.modules.leaderboards.service import Period


class LeaderboardRow(BaseSchema):
    rank: int
    points: int
    # Both empty for a customer who has not chosen to be public.
    name: str | None = None
    handle: str | None = None


class LeaderboardOut(BaseSchema):
    city_id: uuid.UUID
    period: Period
    total_ranked: int
    rows: list[LeaderboardRow]


class MyRank(BaseSchema):
    rank: int
    points: int


class MyStandingOut(BaseSchema):
    city_id: uuid.UUID | None = None
    is_public: bool
    month: MyRank | None = None
    all_time: MyRank | None = None


class VisibilityIn(BaseSchema):
    is_public: bool
