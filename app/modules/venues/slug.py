"""URL slugs for venues: `Kahwa House` -> `kahwa-house`."""

import re
import unicodedata

MAX_SLUG_LENGTH = 120
FALLBACK_SLUG = "venue"

# Curly apostrophes are already dropped by the ASCII step below.
_APOSTROPHES = re.compile(r"['`]")
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def slugify(value: str, *, max_length: int = MAX_SLUG_LENGTH) -> str:
    """Lowercase ASCII words joined by hyphens. Names with no Latin letters fall back to `venue`."""
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    without_apostrophes = _APOSTROPHES.sub("", ascii_value.lower())
    slug = _NON_ALPHANUMERIC.sub("-", without_apostrophes).strip("-")
    return slug[:max_length].rstrip("-") or FALLBACK_SLUG


def with_suffix(slug: str, number: int, *, max_length: int = MAX_SLUG_LENGTH) -> str:
    """`kahwa-house`, 2 -> `kahwa-house-2`, trimming the base so the result still fits."""
    suffix = f"-{number}"
    return slug[: max_length - len(suffix)].rstrip("-") + suffix
