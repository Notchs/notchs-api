import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from app.common.phone import normalize_phone
from app.modules.venue_applications.schemas import ApplicationCreate
from app.modules.venues.models import VenueCategory

TOKEN = "t" * 43


def _form(**overrides: Any) -> ApplicationCreate:
    data: dict[str, Any] = {
        "verification_token": TOKEN,
        "venue_name": "Kahwa House",
        "category": "cafe",
        "city_id": uuid.uuid4(),
        "google_maps_url": "https://maps.app.goo.gl/AbCdEf123",
        "contact_name": "Riya Mehta",
        "phone": "98765 43210",
    }
    return ApplicationCreate(**(data | overrides))


def test_required_fields_only() -> None:
    form = _form()

    assert form.category is VenueCategory.CAFE
    assert form.phone == "+919876543210"
    assert (form.landmark, form.latitude, form.longitude, form.notes) == (None, None, None, None)


def test_all_optional_fields() -> None:
    form = _form(
        landmark=" Opposite Law Garden ",
        latitude="23.022505",
        longitude="72.571362",
        notes="Open late on weekends",
    )

    assert form.landmark == "Opposite Law Garden"
    assert str(form.latitude) == "23.022505"


@pytest.mark.parametrize(
    "field",
    [
        "venue_name",
        "category",
        "city_id",
        "google_maps_url",
        "contact_name",
        "phone",
        "verification_token",
    ],
)
def test_each_required_field_is_required(field: str) -> None:
    data = _form().model_dump()
    del data[field]

    with pytest.raises(ValidationError):
        ApplicationCreate(**data)


def test_email_is_not_a_form_field() -> None:
    assert "email" not in ApplicationCreate.model_fields


@pytest.mark.parametrize(
    "url",
    [
        "https://maps.app.goo.gl/AbCdEf123",
        "https://goo.gl/maps/AbCdEf123",
        "https://www.google.com/maps/place/Kahwa+House/@23.0225,72.5714,17z",
        "https://google.com/maps?q=23.0225,72.5714",
        "https://www.google.co.in/maps/place/Kahwa",
        "https://maps.google.com/?q=Kahwa+House",
    ],
)
def test_accepts_google_maps_links(url: str) -> None:
    assert _form(google_maps_url=url).google_maps_url == url


@pytest.mark.parametrize(
    "url",
    [
        "http://maps.app.goo.gl/AbCdEf123",
        "https://goo.gl/AbCdEf123",
        "https://www.google.com/search?q=kahwa",
        "https://evil.com/maps/place/x",
        "https://google.com.evil.com/maps",
        "https://google.xyz/maps/place/x",
        "https://www.google.de/maps/place/x",
        "https://maps.apple.com/?q=kahwa",
        "javascript:alert(1)",
        "not a link",
        "https://maps.app.goo.gl/" + "a" * 500,
    ],
)
def test_rejects_other_links(url: str) -> None:
    with pytest.raises(ValidationError):
        _form(google_maps_url=url)


@pytest.mark.parametrize(
    "overrides",
    [
        {"venue_name": ""},
        {"venue_name": "x" * 121},
        {"category": "nightclub"},
        {"contact_name": ""},
        {"contact_name": "x" * 61},
        {"landmark": "x" * 121},
        {"notes": "x" * 1001},
        {"latitude": "23.0225"},
        {"latitude": "91", "longitude": "72"},
        {"phone": "12345"},
        {"verification_token": "short"},
    ],
)
def test_rejects_invalid_values(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _form(**overrides)


def test_blank_optional_text_becomes_none() -> None:
    form = _form(landmark="   ", notes="")

    assert (form.landmark, form.notes) == (None, None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9876543210", "+919876543210"),
        ("98765 43210", "+919876543210"),
        ("+91 98765-43210", "+919876543210"),
        ("+919876543210", "+919876543210"),
        ("919876543210", "+919876543210"),
        ("09876543210", "+919876543210"),
        ("0091 98765 43210", "+919876543210"),
        ("+44 20 7946 0958", "+442079460958"),
    ],
)
def test_normalize_phone(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "12345", "5876543210", "+91 12345 67890", "phone", "+0123456789"]
)
def test_normalize_phone_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_phone(raw)
