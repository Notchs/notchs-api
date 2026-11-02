"""The points table and the India-time day, with no database."""

from datetime import UTC, date, datetime

import pytest

from app.common.time import ist_date
from app.modules.scans.models import PointsReason
from app.modules.scans.service import visit_points


@pytest.mark.parametrize(
    ("visit_no", "total"),
    [(1, 12), (2, 10), (3, 12), (4, 10), (5, 10), (6, 12), (7, 10), (9, 12)],
)
def test_points_for_each_visit(visit_no: int, total: int) -> None:
    assert sum(points for _, points in visit_points(visit_no)) == total


def test_first_visit_reasons() -> None:
    assert visit_points(1) == [(PointsReason.VISIT, 10), (PointsReason.NEW_VENUE, 2)]


def test_third_visit_reasons() -> None:
    assert visit_points(3) == [(PointsReason.VISIT, 10), (PointsReason.EVERY_THIRD_VISIT, 2)]


@pytest.mark.parametrize(
    ("utc", "day"),
    [
        (datetime(2026, 11, 1, 18, 29, 59, tzinfo=UTC), date(2026, 11, 1)),
        (datetime(2026, 11, 1, 18, 30, 0, tzinfo=UTC), date(2026, 11, 2)),
    ],
)
def test_the_day_changes_at_midnight_in_india(utc: datetime, day: date) -> None:
    assert ist_date(utc) == day
