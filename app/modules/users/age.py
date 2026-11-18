"""Age from a date of birth: whole years in India, and the range shown to the customer.

The range is worked out each time from `users.birth_date`, never stored, so it moves up on its own.
Venues only ever get counts per range.
"""

from datetime import date

MIN_AGE = 18
# (lowest age, highest age or None for "and over"), in order.
AGE_RANGES: tuple[tuple[int, int | None], ...] = ((18, 24), (25, 34), (35, 44), (45, None))


def age_on(birth_date: date, today: date) -> int:
    before_birthday = (today.month, today.day) < (birth_date.month, birth_date.day)
    return today.year - birth_date.year - before_birthday


def age_range(birth_date: date | None, today: date) -> str | None:
    """E.g. "25-34" or "45+"; None without a date of birth."""
    if birth_date is None:
        return None
    age = age_on(birth_date, today)
    for low, high in AGE_RANGES:
        if age >= low and (high is None or age <= high):
            return f"{low}+" if high is None else f"{low}-{high}"
    return None  # under 18: never saved (checked on save)
