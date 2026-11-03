import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import INSECURE_DEV_JWT_SECRET, INSECURE_DEV_TOTP_KEY, Settings

STRONG = "x" * 40
# A real JWT secret and TOTP key: what every staging/production test starts from.
DEPLOYED = {"JWT_SECRET": STRONG, "TOTP_ENCRYPTION_KEY": Fernet.generate_key().decode()}


def test_local_runs_with_the_dev_defaults() -> None:
    settings = Settings(ENVIRONMENT="local")

    assert settings.JWT_SECRET.get_secret_value() == INSECURE_DEV_JWT_SECRET
    assert settings.EMAIL_DRY_RUN


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("secret", [INSECURE_DEV_JWT_SECRET, "short"])
def test_deployed_environments_need_a_real_jwt_secret(environment: str, secret: str) -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(ENVIRONMENT=environment, JWT_SECRET=secret)


def test_deployed_environments_cannot_disable_turnstile() -> None:
    with pytest.raises(ValidationError, match="TURNSTILE_DISABLED"):
        Settings(ENVIRONMENT="staging", **DEPLOYED, TURNSTILE_DISABLED=True)


def test_production_must_send_real_email() -> None:
    with pytest.raises(ValidationError, match="EMAIL_DRY_RUN"):
        Settings(ENVIRONMENT="production", **DEPLOYED, EMAIL_DRY_RUN=True)


def test_real_email_needs_an_api_key() -> None:
    with pytest.raises(ValidationError, match="RESEND_API_KEY"):
        Settings(ENVIRONMENT="staging", **DEPLOYED, EMAIL_DRY_RUN=False)


def test_valid_production_settings() -> None:
    settings = Settings(
        ENVIRONMENT="production",
        **DEPLOYED,
        EMAIL_DRY_RUN=False,
        RESEND_API_KEY="re_x",
        VENUE_APP_URL="https://venues.notchs.com/",
        ADMIN_APP_URL="https://admin.notchs.com",
        CUSTOMER_APP_URL="https://notchs.com",
        WHATSAPP_DRY_RUN=False,
        WHATSAPP_PHONE_NUMBER_ID="123",
        WHATSAPP_ACCESS_TOKEN="EAA",
        WHATSAPP_APP_SECRET="app-secret",
        WHATSAPP_VERIFY_TOKEN="verify-token",
        WHATSAPP_PUBLIC_NUMBER="919876543210",
    )

    assert settings.is_production
    assert settings.VENUE_APP_URL == "https://venues.notchs.com"


def test_deployed_venue_app_url_must_be_https() -> None:
    with pytest.raises(ValidationError, match="VENUE_APP_URL"):
        Settings(
            ENVIRONMENT="staging",
            **DEPLOYED,
            VENUE_APP_URL="http://venues.notchs.com",
        )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_deployed_environments_need_a_real_totp_key(environment: str) -> None:
    with pytest.raises(ValidationError, match="TOTP_ENCRYPTION_KEY"):
        Settings(
            ENVIRONMENT=environment, JWT_SECRET=STRONG, TOTP_ENCRYPTION_KEY=INSECURE_DEV_TOTP_KEY
        )


def test_totp_key_must_be_a_fernet_key() -> None:
    with pytest.raises(ValidationError, match="Fernet"):
        Settings(TOTP_ENCRYPTION_KEY="not-a-key")


def test_real_whatsapp_sending_needs_the_meta_settings() -> None:
    with pytest.raises(ValidationError, match="WHATSAPP_PHONE_NUMBER_ID"):
        Settings(WHATSAPP_DRY_RUN=False)

    settings = Settings(
        WHATSAPP_DRY_RUN=False, WHATSAPP_PHONE_NUMBER_ID="123", WHATSAPP_ACCESS_TOKEN="EAA"
    )
    assert not settings.WHATSAPP_DRY_RUN


def test_production_must_send_real_whatsapp_messages() -> None:
    with pytest.raises(ValidationError, match="WHATSAPP_DRY_RUN"):
        Settings(ENVIRONMENT="production", **DEPLOYED, EMAIL_DRY_RUN=False, RESEND_API_KEY="re_x")


@pytest.mark.parametrize("version", ["23.0", "v23", "latest"])
def test_whatsapp_api_version_format(version: str) -> None:
    with pytest.raises(ValidationError, match="WHATSAPP_API_VERSION"):
        Settings(WHATSAPP_API_VERSION=version)


def test_production_needs_the_whatsapp_webhook_secrets() -> None:
    with pytest.raises(ValidationError, match="WHATSAPP_APP_SECRET"):
        Settings(
            ENVIRONMENT="production",
            **DEPLOYED,
            EMAIL_DRY_RUN=False,
            RESEND_API_KEY="re_x",
            WHATSAPP_DRY_RUN=False,
            WHATSAPP_PHONE_NUMBER_ID="123",
            WHATSAPP_ACCESS_TOKEN="EAA",
            WHATSAPP_VERIFY_TOKEN="verify-token",
        )


def test_whatsapp_sweep_interval_cannot_be_negative() -> None:
    with pytest.raises(ValidationError, match="WHATSAPP_SWEEP_INTERVAL_SECONDS"):
        Settings(WHATSAPP_SWEEP_INTERVAL_SECONDS=-1)

    assert Settings(WHATSAPP_SWEEP_INTERVAL_SECONDS=0).WHATSAPP_SWEEP_INTERVAL_SECONDS == 0


def test_production_needs_the_public_whatsapp_number() -> None:
    with pytest.raises(ValidationError, match="WHATSAPP_PUBLIC_NUMBER is required"):
        Settings(
            ENVIRONMENT="production",
            **DEPLOYED,
            EMAIL_DRY_RUN=False,
            RESEND_API_KEY="re_x",
            WHATSAPP_DRY_RUN=False,
            WHATSAPP_PHONE_NUMBER_ID="123",
            WHATSAPP_ACCESS_TOKEN="EAA",
            WHATSAPP_APP_SECRET="app-secret",
            WHATSAPP_VERIFY_TOKEN="verify-token",
        )


@pytest.mark.parametrize("number", ["+919876543210", "98765 43210", "0919876543210", "12"])
def test_public_whatsapp_number_is_digits_with_country_code(number: str) -> None:
    with pytest.raises(ValidationError, match="WHATSAPP_PUBLIC_NUMBER must be digits"):
        Settings(WHATSAPP_PUBLIC_NUMBER=number)

    assert Settings(WHATSAPP_PUBLIC_NUMBER="15556755530").WHATSAPP_PUBLIC_NUMBER == "15556755530"


HTTPS_APPS = {
    "VENUE_APP_URL": "https://venues.notchs.com",
    "ADMIN_APP_URL": "https://admin.notchs.com",
    "CUSTOMER_APP_URL": "https://notchs.com",
}


@pytest.mark.parametrize(
    "sender",
    ["Notchs <contact@notchs.com>", "contact@notchs.com", "  Team Notchs <no-reply@notchs.com> "],
)
def test_email_from_on_our_domain(sender: str) -> None:
    settings = Settings(
        ENVIRONMENT="staging",
        **DEPLOYED,
        **HTTPS_APPS,
        EMAIL_DRY_RUN=False,
        RESEND_API_KEY="re_x",
        EMAIL_FROM=sender,
    )

    assert sender == settings.EMAIL_FROM


@pytest.mark.parametrize(
    ("sender", "error"),
    [
        ("Notchs <notchs.app@gmail.com>", "not gmail.com"),
        ("notchs.app@GMAIL.com", "not gmail.com"),
        ("Notchs <someone@yahoo.co.in>", "not yahoo.co.in"),
        ("Notchs notchs.app@gmail.com", "must look like"),
        ("Notchs", "must look like"),
        ("Notchs <a@b@notchs.com>", "must look like"),
    ],
)
def test_email_from_free_mail_or_malformed_is_refused(sender: str, error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        Settings(
            ENVIRONMENT="staging",
            **DEPLOYED,
            **HTTPS_APPS,
            EMAIL_DRY_RUN=False,
            RESEND_API_KEY="re_x",
            EMAIL_FROM=sender,
        )


def test_email_from_is_not_checked_in_dry_run() -> None:
    settings = Settings(ENVIRONMENT="staging", **DEPLOYED, **HTTPS_APPS, EMAIL_FROM="me@gmail.com")

    assert settings.EMAIL_DRY_RUN
