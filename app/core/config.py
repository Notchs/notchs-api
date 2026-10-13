"""Application settings, loaded from environment variables / `.env`."""

import re
from functools import lru_cache
from typing import Literal, Self
from urllib.parse import parse_qsl, urlencode

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

Environment = Literal["local", "development", "staging", "production"]

SCHEMA_NAME_PATTERN = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_LIBPQ_ONLY_PARAMS = frozenset({"channel_binding"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Application
    APP_NAME: str = "Notchs API"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: Environment = "local"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"

    # CORS — JSON list in env, e.g. CORS_ORIGINS=["http://localhost:3000"]
    CORS_ORIGINS: list[str] = []

    # Database (see docs/WORKPLAN.md §0)
    # Connection parts, used to build DATABASE_URL when it is not set. The password is
    # URL-escaped for you, so special characters (@, :, /, #) are fine here.
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_USER: str = "postgres"
    DB_PASSWORD: SecretStr = SecretStr("postgres")
    DB_NAME: str = "notchs"
    # asyncpg `ssl` mode, e.g. `require` for hosted databases. Empty = no SSL param.
    DB_SSL: str | None = None
    # Runtime URL used by the app and worker (`notchs_app` role; may go through a pooler).
    # Takes precedence over the DB_* parts above when set.
    DATABASE_URL: str = ""
    # Direct (unpooled) URL used by Alembic (`notchs_migrator` role). Falls back to DATABASE_URL.
    MIGRATION_DATABASE_URL: str | None = None
    # Postgres schema to use: `public` for main, `dev_<name>` for your own playground.
    DB_SCHEMA: str = "public"
    # True when DATABASE_URL points at PgBouncer/Supavisor in transaction mode.
    DB_USE_POOLER: bool = False
    # Kept small: 4 developers share one hosted DB with a low connection limit.
    DB_POOL_SIZE: int = 2
    DB_MAX_OVERFLOW: int = 3
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800
    DB_STATEMENT_TIMEOUT_MS: int = 30_000
    DB_ECHO: bool = False

    # Redis (see docs/WORKPLAN.md §0.6): each developer runs their own, it is never shared.
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_TIMEOUT_SECONDS: float = 5.0

    @field_validator("REDIS_URL")
    @classmethod
    def _validate_redis_url(cls, value: str) -> str:
        if not value.startswith(("redis://", "rediss://", "unix://")):
            raise ValueError("REDIS_URL must start with redis://, rediss:// or unix://")
        return value

    @field_validator("DATABASE_URL", "MIGRATION_DATABASE_URL")
    @classmethod
    def _normalize_db_url(cls, value: str | None) -> str | None:
        return normalize_database_url(value) if value else value

    @field_validator("DB_SCHEMA")
    @classmethod
    def _validate_db_schema(cls, value: str) -> str:
        # Interpolated into SQL (SET search_path, CREATE SCHEMA), so allow identifiers only.
        if not SCHEMA_NAME_PATTERN.fullmatch(value):
            raise ValueError("DB_SCHEMA must match ^[a-z_][a-z0-9_]{0,62}$")
        return value

    @model_validator(mode="after")
    def _build_database_url(self) -> Self:
        if not self.DATABASE_URL:
            url = URL.create(
                "postgresql+asyncpg",
                username=self.DB_USER,
                password=self.DB_PASSWORD.get_secret_value(),
                host=self.DB_HOST,
                port=self.DB_PORT,
                database=self.DB_NAME,
                query={"ssl": self.DB_SSL} if self.DB_SSL else {},
            )
            self.DATABASE_URL = url.render_as_string(hide_password=False)
        return self

    @property
    def migration_database_url(self) -> str:
        return self.MIGRATION_DATABASE_URL or self.DATABASE_URL

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


def normalize_database_url(url: str) -> str:
    """Make provider URLs (Supabase, Neon, Railway, ...) usable with SQLAlchemy + asyncpg.

    - `postgres://` / `postgresql://` -> `postgresql+asyncpg://`
    - `sslmode=<mode>` -> `ssl=<mode>` (asyncpg does not accept `sslmode`)
    - drops libpq-only params asyncpg rejects (e.g. Neon's `channel_binding`)
    """
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+asyncpg"

    base, qsep, query = rest.partition("?")
    if not qsep:
        return f"{scheme}://{base}"

    params = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key in _LIBPQ_ONLY_PARAMS:
            continue
        params.append(("ssl" if key == "sslmode" else key, value))
    return f"{scheme}://{base}" + (f"?{urlencode(params)}" if params else "")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
