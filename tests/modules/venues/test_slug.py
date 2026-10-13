import re

import pytest

from app.modules.locations.models import SLUG_REGEX
from app.modules.venues.slug import MAX_SLUG_LENGTH, slugify, with_suffix


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Kahwa House", "kahwa-house"),
        ("  Kaafi!  @ SBR ", "kaafi-sbr"),
        ("Riya's Café", "riyas-cafe"),
        ("Zen & Co. 24/7", "zen-co-24-7"),
        ("--Already--Slugged--", "already-slugged"),
    ],
)
def test_slugify(name: str, expected: str) -> None:
    assert slugify(name) == expected


@pytest.mark.parametrize("name", ["કાફે", "!!!", "   "])
def test_slugify_falls_back_when_nothing_usable_is_left(name: str) -> None:
    assert slugify(name) == "venue"


def test_slugify_respects_max_length_without_trailing_hyphen() -> None:
    slug = slugify("a" * 119 + " b")

    assert slug == "a" * 119
    assert len(slug) <= MAX_SLUG_LENGTH


def test_with_suffix() -> None:
    assert with_suffix("kahwa-house", 2) == "kahwa-house-2"


def test_with_suffix_trims_the_base_to_fit() -> None:
    slug = with_suffix("a" * MAX_SLUG_LENGTH, 12)

    assert slug == "a" * (MAX_SLUG_LENGTH - 3) + "-12"
    assert len(slug) == MAX_SLUG_LENGTH


@pytest.mark.parametrize("name", ["Kahwa House", "કાફે", "Zen & Co. 24/7", "a" * 300])
def test_generated_slugs_pass_the_database_check(name: str) -> None:
    assert re.fullmatch(SLUG_REGEX, slugify(name))
    assert re.fullmatch(SLUG_REGEX, with_suffix(slugify(name), 7))
