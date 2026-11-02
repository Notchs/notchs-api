import pytest

from app.modules.users.display import HIDDEN_NAME, first_name, public_name, short_name


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Riya Shah", "Riya S."), ("Riya", "Riya"), ("Mehul  K  patel", "Mehul P."), ("  ", None)],
)
def test_short_name(name: str, expected: str | None) -> None:
    assert short_name(name) == expected


def test_private_customers_are_never_named() -> None:
    assert public_name("Riya Shah", False) == HIDDEN_NAME
    assert public_name(None, True) == HIDDEN_NAME
    assert public_name("Riya Shah", True) == "Riya S."


def test_first_name() -> None:
    assert first_name("Riya Shah") == "Riya"
    assert first_name(None) is None
