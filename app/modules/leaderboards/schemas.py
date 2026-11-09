"""Request and response bodies for leaderboards. Never a phone number."""

import uuid
from datetime import date

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
    # Rank decay (leaderboards/decay.py): the share of their points that counts for rank today
    # (100 = all), and the day it next drops unless they get a Notch first (None = it can't).
    rank_points_percent: int = 100
    rank_drops_on: date | None = None


class VisibilityIn(BaseSchema):
    is_public: bool
