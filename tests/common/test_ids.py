import uuid

import pytest
from pydantic import TypeAdapter
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.common.ids import CROCKFORD_ALPHABET, new_code, new_uuid
from app.common.schemas import PublicCode
from app.db.base import UUIDPrimaryKeyMixin, public_code_column


def test_new_uuid_is_v7_and_time_ordered() -> None:
    ids = [new_uuid() for _ in range(100)]

    assert all(value.version == 7 for value in ids)
    assert ids == sorted(ids)


def test_new_code_format_matches_public_code_schema() -> None:
    code = new_code("VEN")

    assert code.startswith("VEN-")
    assert len(code) == 12
    assert set(code[4:]) <= set(CROCKFORD_ALPHABET)
    assert TypeAdapter(PublicCode).validate_python(code) == code


def test_new_code_is_random() -> None:
    assert len({new_code("VEN") for _ in range(1000)}) == 1000


@pytest.mark.parametrize("prefix", ["ve", "VENU", "ven", "V3N", ""])
def test_new_code_rejects_bad_prefix(prefix: str) -> None:
    with pytest.raises(ValueError):
        new_code(prefix)


class _TestBase(DeclarativeBase):
    """Separate metadata so this model never reaches Alembic autogenerate."""


class _Venue(_TestBase, UUIDPrimaryKeyMixin):
    __tablename__ = "venues"

    name: Mapped[str] = mapped_column()
    venue_code: Mapped[str] = public_code_column("VEN")


def test_model_columns_default_to_uuid7_and_code() -> None:
    table = _Venue.__table__
    id_default = table.c.id.default.arg(None)
    code_default = table.c.venue_code.default.arg(None)

    assert isinstance(id_default, uuid.UUID)
    assert id_default.version == 7
    assert code_default.startswith("VEN-")
    assert table.c.venue_code.unique
    assert not table.c.venue_code.nullable
