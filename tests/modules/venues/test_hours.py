from datetime import datetime

import pytest

from app.modules.locations.time import zone
from app.modules.venues.hours import clock_label, status_at

IST = zone("Asia/Kolkata")
HOURS = {
    "mon": [["09:00", "23:00"]],
    "tue": [["09:00", "23:00"]],
    "fri": [["18:00", "02:00"]],  # past midnight
    "sat": [["10:00", "14:00"], ["18:00", "23:30"]],
}


def at(day: int, hour: int, minute: int = 0) -> datetime:
    # 2026-10-19 is a Monday.
    return datetime(2026, 10, 19 + day, hour, minute, tzinfo=IST)


@pytest.mark.parametrize(
    ("value", "label"),
    [("23:00", "11 PM"), ("08:30", "8:30 AM"), ("00:00", "12 AM"), ("12:00", "12 PM")],
)
def test_clock_label(value: str, label: str) -> None:
    assert clock_label(value) == label


def test_open_until_closing_time() -> None:
    status = status_at(HOURS, at(0, 12))
    assert (status.is_open, status.label, status.closes_at) == (True, "Open until 11 PM", "23:00")


def test_closed_later_today_opens_tomorrow() -> None:
    status = status_at(HOURS, at(0, 23, 30))
    assert (status.is_open, status.label) == (False, "Opens Tue 9 AM")


def test_closed_before_opening_today() -> None:
    assert status_at(HOURS, at(0, 7)).label == "Opens 9 AM"


def test_a_range_past_midnight_is_open_the_next_morning() -> None:
    status = status_at(HOURS, at(5, 1, 30))  # Saturday 01:30, from Friday's 18:00-02:00
    assert (status.is_open, status.label) == (True, "Open until 2 AM")


def test_between_two_ranges_on_one_day() -> None:
    assert status_at(HOURS, at(5, 15)).label == "Opens 6 PM"


def test_skips_closed_days() -> None:
    assert status_at(HOURS, at(1, 23, 30)).label == "Opens Fri 6 PM"


@pytest.mark.parametrize("hours", [None, {}, {"mon": []}])
def test_no_hours_means_unknown(hours: dict | None) -> None:
    status = status_at(hours, at(0, 12))
    assert (status.is_open, status.label) == (None, None)
