from datetime import timedelta

import pytest

from app.core.config import settings
from app.modules.auth.emails import is_disposable, normalize_email
from app.modules.auth.enums import TokenType
from app.modules.auth.tokens import hash_token, lifetime, new_token


def test_tokens_are_random_url_safe_and_long() -> None:
    tokens = {new_token() for _ in range(1000)}

    assert len(tokens) == 1000
    assert all(len(t) >= 43 and t.replace("-", "").replace("_", "").isalnum() for t in tokens)


def test_hash_is_sha256_hex_and_stable() -> None:
    raw = new_token()

    assert len(hash_token(raw)) == 64
    assert hash_token(raw) == hash_token(raw)
    assert hash_token(new_token()) != hash_token(raw)


@pytest.mark.parametrize(
    ("token_type", "expected"),
    [
        (TokenType.MAGIC_LINK, timedelta(minutes=15)),
        (TokenType.DEVICE_PAIRING, timedelta(minutes=10)),
        (TokenType.SET_PASSWORD, timedelta(hours=72)),
        (TokenType.PASSWORD_RESET, timedelta(minutes=30)),
        (TokenType.REFRESH, timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS)),
    ],
)
def test_lifetimes_match_the_plan(token_type: TokenType, expected: timedelta) -> None:
    assert lifetime(token_type) == expected


def test_normalize_email() -> None:
    assert normalize_email("  Owner@Kahwa.IN ") == "owner@kahwa.in"


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("someone@mailinator.com", True),
        ("Someone@MAILINATOR.COM", True),
        ("someone@x.mailinator.com", True),
        ("owner@kahwa.in", False),
        ("owner@gmail.com", False),
        ("owner@notmailinator.com", False),
    ],
)
def test_is_disposable(email: str, expected: bool) -> None:
    assert is_disposable(email) is expected
