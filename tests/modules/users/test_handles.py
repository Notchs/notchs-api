"""Customer names from WhatsApp and the handles made from them."""

import re

import pytest

from app.modules.users import handles


@pytest.mark.parametrize(
    ("profile", "name"),
    [
        ("Riya Mehta", "Riya Mehta"),
        ("  Riya   Mehta  ", "Riya Mehta"),
        ("Riya\nMehta\t", "Riya Mehta"),
        ("Riya\x00\x07 M", "Riya M"),
        ("રિયા મહેતા", "રિયા મહેતા"),  # kept as written: only the handle is ASCII
        ("🌸 Riya 🌸", "🌸 Riya 🌸"),
        ("x" * 80, "x" * 60),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_clean_name(profile: str | None, name: str | None) -> None:
    assert handles.clean_name(profile) == name


@pytest.mark.parametrize(
    ("name", "base"),
    [
        ("Riya Mehta", "riya.m"),
        ("Riya", "riya"),
        ("Riya Ben Mehta", "riya.m"),
        ("Rīyā Mēhtā", "riya.m"),
        ("O'Brien Shah", "obrien.s"),
        ("J Shah", "shah"),
        ("Maximiliananderson Shah", "maximilianander.s"),
        ("રિયા", "notcher"),
        ("🌸", "notcher"),
        ("J", "notcher"),
        (None, "notcher"),
    ],
)
def test_handle_base(name: str | None, base: str) -> None:
    assert handles.handle_base(name) == base


def test_random_handles_fit_the_rules() -> None:
    for base in ("riya.m", "maximilianander.s", "notcher"):
        for digits in (handles.SHORT_DIGITS, handles.LONG_DIGITS):
            handle = handles.random_handle(base, digits)
            assert re.fullmatch(handles.HANDLE_REGEX, handle), handle
            assert len(handle) <= handles.MAX_HANDLE_LENGTH
            assert handle[len(base)] != "0"  # the number never starts with 0


def test_random_handles_differ() -> None:
    assert len({handles.random_handle("riya.m", 4) for _ in range(30)}) > 20
