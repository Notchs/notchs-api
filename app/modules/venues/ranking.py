"""How places are ranked on the public map (user decision, 27 Nov 2026; numbers provisional).

A place's score comes from real, valid visits only (revoked visits never count):

    a visit in the last 30 days      counts 100%
    then each week                   10% less (90%, 80%... ), so after about 3 months it stops
    one customer at one place        counts once a day (the visits table already enforces it),
                                     and adds at most 8 visits' worth to that place's score,
                                     so staff or one regular can't push a place up
    ties                             the place with more different customers ranks higher;
                                     still equal, they share the rank (1, 2, 2, 4)

Places are ranked within their city and kind (cafes against cafes), among the places the map
shows. A place with no counting visits has no rank. Ranks are live: there is no reset on the
1st. Visit counts are never sent to the public, only the rank.
"""

from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import ColumnElement, Date, Integer, case, cast, func, literal

WINDOW_DAYS = 30
FADE_STEP_DAYS = 7
FADE_PERCENT_PER_STEP = 10
MAX_VISITS_PER_CUSTOMER = 8

FULL = 100  # a visit's weight in percent
# Ages 0 to 92 days still count something; at 93 days a visit has faded to 0%.
COUNTED_DAYS = WINDOW_DAYS + FADE_STEP_DAYS * (FULL // FADE_PERCENT_PER_STEP - 1)
# One customer's most at one place, in percent points.
MAX_PER_CUSTOMER = MAX_VISITS_PER_CUSTOMER * FULL


def visit_weight(age_days: int) -> int:
    """A visit's weight in percent, `age_days` after it (0 = today). Mirrors `weight_sql`."""
    if age_days < WINDOW_DAYS:
        return FULL
    steps = (age_days - WINDOW_DAYS) // FADE_STEP_DAYS + 1
    return max(0, FULL - FADE_PERCENT_PER_STEP * steps)


def weight_sql(visit_date: ColumnElement[date], today: date) -> ColumnElement[int]:
    """`visit_weight` in SQL, for a `visits.visit_date` column."""
    # date - date is whole days in Postgres; the cast keeps the division below whole too.
    age = cast(literal(today, Date) - visit_date, Integer)
    steps = (age - WINDOW_DAYS) // FADE_STEP_DAYS + 1
    return case(
        (age < WINDOW_DAYS, FULL),
        else_=func.greatest(0, FULL - FADE_PERCENT_PER_STEP * steps),
    )


def first_counted_day(today: date) -> date:
    return today - timedelta(days=COUNTED_DAYS - 1)


@dataclass(frozen=True)
class Score:
    points: int  # percent points: 100 = one full visit
    customers: int  # different customers with a counting visit


def assign_ranks[K: Hashable](places: Iterable[tuple[K, str, Score | None]]) -> dict[K, int]:
    """Ranks per kind of place, from `(key, category, score)`. Places without a score (or a
    zero one) get no entry. Equal scores and customer counts share a rank: 1, 2, 2, 4."""
    by_category: dict[str, list[tuple[K, tuple[int, int]]]] = {}
    for key, category, score in places:
        if score is not None and score.points > 0:
            by_category.setdefault(category, []).append((key, (score.points, score.customers)))
    ranks: dict[K, int] = {}
    for scored in by_category.values():
        scored.sort(key=lambda item: item[1], reverse=True)
        for position, (key, value) in enumerate(scored, start=1):
            previous = scored[position - 2] if position > 1 else None
            if previous is not None and previous[1] == value:
                ranks[key] = ranks[previous[0]]
            else:
                ranks[key] = position
    return ranks
