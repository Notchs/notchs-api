"""Rank decay (team decision, provisional: ranks drop after 15 days without a Notch).

The exact rate wasn't decided, so this is the rule until the team changes these numbers:

    up to 15 days since the last Notch    rank points are 100% of the points earned
    day 16 to 22                          90%
    day 23 to 29                          80%, and so on: 10% less each week, down to 0%
    the next Notch                        back to 100% straight away

Only the leaderboards use it (both all-time and this month). The points a customer earned never
change: the passport still shows them all, and `points_ledger` is untouched, so changing the rule
or turning it off just takes a rebuild.
"""

from datetime import date, timedelta

DECAY_AFTER_DAYS = 15
DECAY_STEP_DAYS = 7
DECAY_PERCENT_PER_STEP = 10


def _steps(last_notch: date, today: date) -> int:
    idle = (today - last_notch).days
    if idle <= DECAY_AFTER_DAYS:
        return 0
    return (idle - DECAY_AFTER_DAYS - 1) // DECAY_STEP_DAYS + 1


def kept_percent(last_notch: date | None, today: date) -> int:
    """How much of their points count for rank today. 100 without any Notch yet (nothing to
    decay from) or within 15 days of the last one."""
    if last_notch is None:
        return 100
    return max(0, 100 - DECAY_PERCENT_PER_STEP * _steps(last_notch, today))


def next_drop_on(last_notch: date | None, today: date) -> date | None:
    """The day rank points next drop if they get no Notch before it; None if they can't drop."""
    if last_notch is None or kept_percent(last_notch, today) == 0:
        return None
    steps = _steps(last_notch, today)
    return last_notch + timedelta(days=DECAY_AFTER_DAYS + 1 + steps * DECAY_STEP_DAYS)


def rank_points(points: int, kept: int) -> int:
    """Whole points: rounded down."""
    return points * kept // 100
