"""Application settings, loaded from environment variables / `.env`."""

import re
from functools import lru_cache
from typing import Literal, Self
from urllib.parse import parse_qsl, urlencode

from cryptography.fernet import Fernet
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

Environment = Literal["local", "development", "staging", "production"]

SCHEMA_NAME_PATTERN = re.compile(r"[a-z_][a-z0-9_]{0,62}")
INSECURE_DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-do-not-deploy"
# A valid Fernet key that is public, so it only works outside staging/production.
INSECURE_DEV_TOTP_KEY = "ZGV2LW9ubHktaW5zZWN1cmUtdG90cC1rZXktMDAwMDA="
MIN_JWT_SECRET_LENGTH = 32
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

    # Database (rules: README, "Database")
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

    # Redis (README, "Redis"): each developer runs their own, it is never shared.
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_TIMEOUT_SECONDS: float = 5.0

    # Auth (README, "Auth, email, CAPTCHA and WhatsApp settings"). The default secret is
    # for local work: staging and production refuse to start with it.
    JWT_SECRET: SecretStr = SecretStr(INSECURE_DEV_JWT_SECRET)
    JWT_ALGORITHM: Literal["HS256"] = "HS256"
    JWT_ISSUER: str = "notchs-api"
    ACCESS_TOKEN_TTL_MINUTES: int = 15
    REFRESH_TOKEN_TTL_DAYS: int = 30
    # Encrypts counter phones' TOTP secrets in the database (Fernet key: 32 bytes, base64).
    # Generate one: see .env.example.
    TOTP_ENCRYPTION_KEY: SecretStr = SecretStr(INSECURE_DEV_TOTP_KEY)

    # Email (Resend). With EMAIL_DRY_RUN=true nothing is sent: emails are written to the log.
    EMAIL_DRY_RUN: bool = True
    RESEND_API_KEY: SecretStr | None = None
    EMAIL_FROM: str = "Notchs <no-reply@notchs.com>"
    # Local and development only: send to this SMTP server (Mailpit) instead of Resend.
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 1025

    # Where emailed links point: the venue app's /set-password page, for example.
    VENUE_APP_URL: str = "http://localhost:3000"
    ADMIN_APP_URL: str = "http://localhost:3001"
    # The customer site; the WhatsApp login link opens its /login/verify page.
    CUSTOMER_APP_URL: str = "http://localhost:3002"
    # Optional: who gets a "New venue application" email. Empty = nobody.
    ADMIN_NOTIFY_EMAIL: str | None = None

    # Cloudflare Turnstile (CAPTCHA) on public forms. TURNSTILE_DISABLED is for local work only.
    TURNSTILE_SECRET: SecretStr | None = None
    TURNSTILE_DISABLED: bool = False

    # WhatsApp Cloud API (Meta). With WHATSAPP_DRY_RUN=true nothing is sent: replies go to the log.
    WHATSAPP_DRY_RUN: bool = True
    WHATSAPP_PHONE_NUMBER_ID: str | None = None
    WHATSAPP_ACCESS_TOKEN: SecretStr | None = None
    WHATSAPP_API_VERSION: str = "v23.0"
    WHATSAPP_GRAPH_URL: str = "https://graph.facebook.com"
    # Webhook: Meta signs every call with the app secret (App settings -> Basic). The verify
    # token is any random string, entered in Meta's "Configure Webhooks" form too.
    WHATSAPP_APP_SECRET: SecretStr | None = None
    WHATSAPP_VERIFY_TOKEN: SecretStr | None = None
    # The number customers message, digits only with the country code (e.g. 15556755530 for the
    # test number). Counter phones put it in their scan QR.
    WHATSAPP_PUBLIC_NUMBER: str | None = None

    # The most requests one visitor (IP address) may make in a minute, over the whole API: a
    # backstop behind the per-feature limits. 0 turns it off. Health checks and Meta's webhook
    # are not counted.
    GLOBAL_RATE_LIMIT_PER_MINUTE: int = 600
    # The largest request body the API reads (the WhatsApp webhook has its own limit).
    MAX_REQUEST_BODY_BYTES: int = 1_000_000

    # An admin made from settings, for a deploy with no shell to run `create-admin`
    # (admin_users/bootstrap.py). At startup, if no admin has this email, one is created.
    # With FIRST_ADMIN_PASSWORD it can log in straight away (no email needed); without it, it is
    # emailed a set-password link. An account that already has a password is never touched.
    FIRST_ADMIN_EMAIL: str | None = None
    FIRST_ADMIN_NAME: str = "Notchs admin"
    FIRST_ADMIN_PASSWORD: SecretStr | None = None
    # Public https image sent with a stamp. Empty = the stamp is a text message.
    WHATSAPP_STAMP_IMAGE_URL: str | None = None
    # How often the API looks for incoming messages left unhandled (e.g. after a restart).
    # 0 turns the sweep off.
    WHATSAPP_SWEEP_INTERVAL_SECONDS: float = 60
    # How often leaderboards are rebuilt from the ledger, from startup on. 0 turns it off.
    LEADERBOARD_REBUILD_INTERVAL_SECONDS: float = 3600

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

    @model_validator(mode="after")
    def _check_deployed_auth_settings(self) -> Self:
        """Refuse to start staging/production with development-only auth settings."""
        if self.ENVIRONMENT not in ("staging", "production"):
            return self
        secret = self.JWT_SECRET.get_secret_value()
        if secret == INSECURE_DEV_JWT_SECRET or len(secret) < MIN_JWT_SECRET_LENGTH:
            raise ValueError(f"JWT_SECRET must be at least {MIN_JWT_SECRET_LENGTH} characters")
        if self.TOTP_ENCRYPTION_KEY.get_secret_value() == INSECURE_DEV_TOTP_KEY:
            raise ValueError("TOTP_ENCRYPTION_KEY must be set to a real Fernet key")
        if self.TURNSTILE_DISABLED:
            raise ValueError("TURNSTILE_DISABLED is only allowed in local and development")
        if self.SMTP_HOST:
            raise ValueError("SMTP_HOST is only allowed in local and development")
        if not self.EMAIL_DRY_RUN and self.RESEND_API_KEY is None:
            raise ValueError("RESEND_API_KEY is required when EMAIL_DRY_RUN is false")
        if self.is_production and self.WHATSAPP_DRY_RUN:
            raise ValueError("WHATSAPP_DRY_RUN must be false in production")
        if self.is_production and not (self.WHATSAPP_APP_SECRET and self.WHATSAPP_VERIFY_TOKEN):
            # Without them every webhook call from Meta is refused: no message would arrive.
            raise ValueError(
                "WHATSAPP_APP_SECRET and WHATSAPP_VERIFY_TOKEN are required in production"
            )
        if self.is_production and not self.WHATSAPP_PUBLIC_NUMBER:
            # Without it the counter phones cannot show a scan QR.
            raise ValueError("WHATSAPP_PUBLIC_NUMBER is required in production")
        if self.is_production and self.EMAIL_DRY_RUN:
            raise ValueError("EMAIL_DRY_RUN must be false in production")
        for name in ("VENUE_APP_URL", "ADMIN_APP_URL", "CUSTOMER_APP_URL"):
            if not getattr(self, name).startswith("https://"):
                raise ValueError(f"{name} must be an https:// address outside local/development")
        return self

    @field_validator("VENUE_APP_URL", "ADMIN_APP_URL", "CUSTOMER_APP_URL")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def _check_whatsapp_settings(self) -> Self:
        if not self.WHATSAPP_DRY_RUN and not (
            self.WHATSAPP_PHONE_NUMBER_ID and self.WHATSAPP_ACCESS_TOKEN
        ):
            raise ValueError(
                "WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_ACCESS_TOKEN are required when "
                "WHATSAPP_DRY_RUN is false"
            )
        if self.WHATSAPP_PUBLIC_NUMBER is not None and not re.fullmatch(
            r"[1-9][0-9]{7,14}", self.WHATSAPP_PUBLIC_NUMBER
        ):
            raise ValueError("WHATSAPP_PUBLIC_NUMBER must be digits with the country code")
        if self.WHATSAPP_STAMP_IMAGE_URL and not self.WHATSAPP_STAMP_IMAGE_URL.startswith(
            "https://"
        ):
            raise ValueError("WHATSAPP_STAMP_IMAGE_URL must be an https:// address")
        if not re.fullmatch(r"v\d+\.\d+", self.WHATSAPP_API_VERSION):
            raise ValueError("WHATSAPP_API_VERSION must look like v23.0")
        if self.LEADERBOARD_REBUILD_INTERVAL_SECONDS < 0:
            raise ValueError("LEADERBOARD_REBUILD_INTERVAL_SECONDS must be 0 (off) or more")
        if self.WHATSAPP_SWEEP_INTERVAL_SECONDS < 0:
            raise ValueError("WHATSAPP_SWEEP_INTERVAL_SECONDS must be 0 (off) or more")
        return self

    @field_validator("FIRST_ADMIN_EMAIL")
    @classmethod
    def _first_admin_email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        email = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("FIRST_ADMIN_EMAIL must be an email address")
        return email

    @field_validator("TOTP_ENCRYPTION_KEY")
    @classmethod
    def _check_fernet_key(cls, value: SecretStr) -> SecretStr:
        try:
            Fernet(value.get_secret_value())
        except ValueError as exc:
            raise ValueError("TOTP_ENCRYPTION_KEY must be a Fernet key (32 bytes, base64)") from exc
        return value

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
