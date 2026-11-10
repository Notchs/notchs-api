import pytest

from app.modules.auth.exceptions import PasswordInvalidException
from app.modules.auth.passwords import (
    check_password_policy,
    hash_password,
    hash_password_async,
    needs_rehash,
    verify_dummy,
    verify_password,
    verify_password_async,
)


def test_hash_is_argon2id_and_salted() -> None:
    first, second = hash_password("correct horse battery"), hash_password("correct horse battery")

    assert first.startswith("$argon2id$")
    assert first != second
    assert not needs_rehash(first)


def test_verify_accepts_right_and_rejects_wrong_password() -> None:
    password_hash = hash_password("correct horse battery")

    assert verify_password(password_hash, "correct horse battery")
    assert not verify_password(password_hash, "Correct horse battery")
    assert not verify_password(password_hash, "")


def test_verify_returns_false_for_a_broken_hash() -> None:
    assert not verify_password("not-a-hash", "anything at all")


def test_dummy_verify_runs_without_raising() -> None:
    verify_dummy("whatever the user typed")


async def test_async_helpers() -> None:
    password_hash = await hash_password_async("correct horse battery")

    assert await verify_password_async(password_hash, "correct horse battery")


@pytest.mark.parametrize("password", ["a" * 10, "a" * 128, "ten chars!", "पासवर्ड१२३४५६"])
def test_policy_accepts(password: str) -> None:
    check_password_policy(password, email="owner@kahwa.in")


@pytest.mark.parametrize("password", ["", "a" * 9, "a" * 129])
def test_policy_rejects_length(password: str) -> None:
    with pytest.raises(PasswordInvalidException):
        check_password_policy(password)


@pytest.mark.parametrize("password", ["owner@kahwa.in", " Owner@Kahwa.IN "])
def test_policy_rejects_the_email_itself(password: str) -> None:
    with pytest.raises(PasswordInvalidException):
        check_password_policy(password, email="owner@kahwa.in")
