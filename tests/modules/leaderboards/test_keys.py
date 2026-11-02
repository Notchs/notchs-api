"""Board keys and months, with no database or Redis."""

import uuid
from datetime import UTC, datetime

from app.common.time import IST
from app.modules.leaderboards.service import board_key, month_of, month_start


def test_board_keys() -> None:
    city = uuid.UUID(int=1)

    assert board_key(city, "all") == f"lb:{city}:all"
    assert board_key(city, "2026-11") == f"lb:{city}:2026-11"


def test_the_month_is_counted_in_india() -> None:
    assert month_of(datetime(2026, 11, 30, 18, 29, tzinfo=UTC)) == "2026-11"
    assert month_of(datetime(2026, 11, 30, 18, 30, tzinfo=UTC)) == "2026-12"


def test_month_start_is_midnight_on_the_first_in_india() -> None:
    late_on_the_31st_utc = datetime(2026, 12, 31, 18, 30, tzinfo=UTC)  # already 1 Jan in India

    assert month_start(late_on_the_31st_utc) == datetime(2027, 1, 1, tzinfo=IST)
