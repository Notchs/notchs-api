"""The points table and the India-time day, with no database."""

from datetime import UTC, date, datetime

import pytest

from app.common.time import ist_date
from app.modules.scans.models import PointsReason
from app.modules.scans.niches import NICHES
from app.modules.scans.service import visit_points


@pytest.mark.parametrize(
    ("visit_no", "total"),
    [(1, 12), (2, 10), (3, 12), (4, 10), (5, 10), (6, 12), (7, 10), (9, 12)],
)
def test_points_for_each_visit(visit_no: int, total: int) -> None:
    assert sum(points for _, points in visit_points(visit_no, "cafe")) == total


def test_first_visit_reasons() -> None:
    assert visit_points(1, "cafe") == [(PointsReason.VISIT, 10), (PointsReason.NEW_VENUE, 2)]


def test_third_visit_reasons() -> None:
    assert visit_points(3, "cafe") == [
        (PointsReason.VISIT, 10),
        (PointsReason.EVERY_THIRD_VISIT, 2),
    ]


def test_every_category_is_a_niche_with_points() -> None:
    from app.modules.venues.models import VenueCategory

    assert set(NICHES) == set(VenueCategory)
    assert all(niche.visit_points > 0 for niche in NICHES.values())


def test_decided_niche_colours() -> None:
    assert (NICHES["cafe"].colour, NICHES["restaurant"].colour, NICHES["turf_gym"].colour) == (
        "blue",
        "violet",
        "green",
    )


@pytest.mark.parametrize(
    ("utc", "day"),
    [
        (datetime(2026, 11, 1, 18, 29, 59, tzinfo=UTC), date(2026, 11, 1)),
        (datetime(2026, 11, 1, 18, 30, 0, tzinfo=UTC), date(2026, 11, 2)),
    ],
)
def test_the_day_changes_at_midnight_in_india(utc: datetime, day: date) -> None:
    assert ist_date(utc) == day
