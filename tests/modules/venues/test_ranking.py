"""The map's ranking rules, no database (venues/ranking.py). SQL side: test_venue_map.py."""

from datetime import date

import pytest

from app.modules.venues.ranking import (
    COUNTED_DAYS,
    MAX_PER_CUSTOMER,
    Score,
    assign_ranks,
    first_counted_day,
    visit_weight,
)


@pytest.mark.parametrize(
    ("age", "weight"),
    [
        (0, 100),  # today
        (29, 100),  # still inside the 30 days
        (30, 90),  # first week after
        (36, 90),
        (37, 80),
        (85, 20),
        (92, 10),
        (93, 0),  # faded out
        (400, 0),
    ],
)
def test_a_visit_fades_10_percent_a_week_after_30_days(age: int, weight: int) -> None:
    assert visit_weight(age) == weight


def test_the_counted_days_end_where_the_weight_reaches_zero() -> None:
    assert visit_weight(COUNTED_DAYS - 1) > 0
    assert visit_weight(COUNTED_DAYS) == 0
    assert first_counted_day(date(2026, 11, 28)) == date(2026, 8, 28)


def test_one_customer_adds_at_most_8_visits() -> None:
    assert MAX_PER_CUSTOMER == 800


def test_more_points_rank_higher() -> None:
    ranks = assign_ranks([("a", "cafe", Score(300, 3)), ("b", "cafe", Score(900, 5))])
    assert ranks == {"b": 1, "a": 2}


def test_on_equal_points_more_customers_rank_higher() -> None:
    ranks = assign_ranks([("few", "cafe", Score(800, 1)), ("many", "cafe", Score(800, 8))])
    assert ranks == {"many": 1, "few": 2}


def test_fully_equal_places_share_a_rank_and_the_next_one_skips() -> None:
    ranks = assign_ranks(
        [
            ("a", "cafe", Score(500, 5)),
            ("b", "cafe", Score(400, 4)),
            ("c", "cafe", Score(400, 4)),
            ("d", "cafe", Score(100, 1)),
        ]
    )
    assert ranks == {"a": 1, "b": 2, "c": 2, "d": 4}


def test_each_kind_of_place_has_its_own_ranks() -> None:
    ranks = assign_ranks(
        [
            ("cafe", "cafe", Score(100, 1)),
            ("big-restaurant", "restaurant", Score(5000, 50)),
        ]
    )
    assert ranks == {"cafe": 1, "big-restaurant": 1}


def test_places_without_counting_visits_have_no_rank() -> None:
    ranks = assign_ranks([("none", "cafe", None), ("zero", "cafe", Score(0, 0))])
    assert ranks == {}
