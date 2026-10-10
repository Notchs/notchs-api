import pytest
from pydantic import ValidationError
from sqlalchemy import make_url

from app.core.config import Settings, normalize_database_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgres://u:p@host:5432/db", "postgresql+asyncpg://u:p@host:5432/db"),
        ("postgresql://u:p@host/db", "postgresql+asyncpg://u:p@host/db"),
        ("postgresql+asyncpg://u:p@host/db", "postgresql+asyncpg://u:p@host/db"),
        (
            "postgresql://u:p@host/db?sslmode=require&channel_binding=require",
            "postgresql+asyncpg://u:p@host/db?ssl=require",
        ),
    ],
)
def test_normalize_database_url(url: str, expected: str) -> None:
    assert normalize_database_url(url) == expected


def test_database_url_built_from_parts_escapes_password() -> None:
    settings = Settings(
        DATABASE_URL="",
        DB_HOST="db.example.com",
        DB_PORT=6543,
        DB_USER="cw_app",
        DB_PASSWORD="p@ss:w/rd#1",
        DB_NAME="city_wrapped",
        DB_SSL="require",
    )

    url = make_url(settings.DATABASE_URL)
    assert url.drivername == "postgresql+asyncpg"
    assert url.password == "p@ss:w/rd#1"
    assert (url.host, url.port, url.database) == ("db.example.com", 6543, "city_wrapped")
    assert url.query == {"ssl": "require"}


def test_database_url_wins_over_parts() -> None:
    settings = Settings(DATABASE_URL="postgres://u:p@other/db", DB_HOST="ignored")

    assert settings.DATABASE_URL == "postgresql+asyncpg://u:p@other/db"


def test_migration_url_falls_back_to_database_url() -> None:
    settings = Settings(DATABASE_URL="postgres://u:p@host/db", MIGRATION_DATABASE_URL="")

    assert settings.migration_database_url == "postgresql+asyncpg://u:p@host/db"


@pytest.mark.parametrize("schema", ["public", "dev_harsh", "test_1a2b"])
def test_valid_db_schema(schema: str) -> None:
    assert schema == Settings(DB_SCHEMA=schema).DB_SCHEMA


@pytest.mark.parametrize(
    "schema", ["Dev_Harsh", "dev-harsh", "1dev", 'x"; DROP SCHEMA public; --', ""]
)
def test_invalid_db_schema_rejected(schema: str) -> None:
    with pytest.raises(ValidationError):
        Settings(DB_SCHEMA=schema)
