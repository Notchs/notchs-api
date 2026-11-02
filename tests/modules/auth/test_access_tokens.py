import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import settings
from app.modules.auth.access_tokens import create_access_token, decode_access_token
from app.modules.auth.enums import SubjectType
from app.modules.auth.exceptions import NotAuthenticatedException


def test_round_trip_for_a_venue_member() -> None:
    member_id, venue_id = uuid.uuid4(), uuid.uuid4()

    token = create_access_token(SubjectType.VENUE_MEMBER, member_id, venue_id=venue_id)
    claims = decode_access_token(token, expected_type=SubjectType.VENUE_MEMBER)

    assert (claims.subject_id, claims.venue_id) == (member_id, venue_id)
    assert claims.subject_type is SubjectType.VENUE_MEMBER


def test_lifetime_comes_from_settings() -> None:
    now = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    token = create_access_token(SubjectType.ADMIN, uuid.uuid4(), now=now)
    payload = jwt.decode(token, options={"verify_signature": False})

    assert payload["exp"] - payload["iat"] == settings.ACCESS_TOKEN_TTL_MINUTES * 60
    assert "vid" not in payload


def test_expired_token_is_rejected() -> None:
    old = datetime.now(UTC) - timedelta(minutes=settings.ACCESS_TOKEN_TTL_MINUTES + 1)
    token = create_access_token(SubjectType.USER, uuid.uuid4(), now=old)

    with pytest.raises(NotAuthenticatedException):
        decode_access_token(token, expected_type=SubjectType.USER)


def test_token_for_another_subject_type_is_rejected() -> None:
    token = create_access_token(SubjectType.USER, uuid.uuid4())

    with pytest.raises(NotAuthenticatedException):
        decode_access_token(token, expected_type=SubjectType.VENUE_MEMBER)


def _forge(claims: dict[str, object], *, key: str | None = None, algorithm: str = "HS256") -> str:
    secret = key or settings.JWT_SECRET.get_secret_value()
    return jwt.encode(claims, secret, algorithm=algorithm)


def _valid_claims() -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "iss": settings.JWT_ISSUER,
        "sub": str(uuid.uuid4()),
        "typ": "admin",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not.a.jwt",
        _forge(_valid_claims(), key="a-different-secret-of-at-least-32-chars"),
        _forge(_valid_claims() | {"iss": "someone-else"}),
        _forge(_valid_claims() | {"sub": "not-a-uuid"}),
        _forge({k: v for k, v in _valid_claims().items() if k != "exp"}),
        _forge({k: v for k, v in _valid_claims().items() if k != "typ"}),
        jwt.encode(_valid_claims(), key="", algorithm="none"),
    ],
)
def test_tampered_or_incomplete_tokens_are_rejected(token: str) -> None:
    with pytest.raises(NotAuthenticatedException):
        decode_access_token(token, expected_type=SubjectType.ADMIN)
