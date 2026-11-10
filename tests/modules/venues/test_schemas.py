import uuid
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.modules.venues.models import VenueCategory
from app.modules.venues.schemas import VenueCreate, VenueUpdate


def _create(**overrides: Any) -> VenueCreate:
    data: dict[str, Any] = {"city_id": uuid.uuid4(), "name": "Kahwa House", "category": "cafe"}
    return VenueCreate(**(data | overrides))


def test_create_applies_spec_defaults() -> None:
    venue = _create()

    assert venue.category is VenueCategory.CAFE
    assert venue.geofence_radius_m == 150
    assert venue.first_notch_spots == 50
    assert venue.slug is None


def test_create_strips_the_name() -> None:
    assert _create(name="  Kahwa House ").name == "Kahwa House"


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "x" * 121},
        {"category": "nightclub"},
        {"slug": "Kahwa House"},
        {"slug": "kahwa--house"},
        {"description": "x" * 161},
        {"geofence_radius_m": 0},
        {"first_notch_spots": -1},
        {"founding_venue_no": 0},
        {"founding_venue_no": 201},  # more than any kind's founding spots
        {"monthly_fee_paise": -1},
        {"latitude": "23.0225"},
        {"longitude": "72.5714"},
        {"latitude": "91", "longitude": "72.5714"},
        {"latitude": "23.0225", "longitude": "181"},
        {"latitude": "23.02250001", "longitude": "72.5714"},
    ],
)
def test_create_rejects_invalid_input(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _create(**overrides)


def test_create_accepts_coordinates_together() -> None:
    venue = _create(latitude="23.022505", longitude="72.571362")

    assert (venue.latitude, venue.longitude) == (Decimal("23.022505"), Decimal("72.571362"))


def test_opening_hours_accepts_the_spec_shape() -> None:
    hours = {"mon": [["08:00", "23:00"]], "tue": [], "fri": [["18:00", "01:30"]]}

    venue = _create(opening_hours=hours)

    # Tuples in Python, the same lists again once dumped to JSON for the JSONB column.
    assert venue.model_dump(mode="json")["opening_hours"] == hours


@pytest.mark.parametrize(
    "hours",
    [
        {"monday": [["08:00", "23:00"]]},
        {"mon": [["8:00", "23:00"]]},
        {"mon": [["08:00", "24:00"]]},
        {"mon": [["08:00"]]},
        {"mon": [["08:00", "08:00"]]},
        {"mon": [["08:00", "09:00"]] * 5},
        {"mon": "08:00-23:00"},
    ],
)
def test_opening_hours_rejects_bad_shapes(hours: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _create(opening_hours=hours)


def test_update_only_carries_the_fields_that_were_sent() -> None:
    update = VenueUpdate(description="Best filter coffee in SBR", area_id=None)

    assert update.model_dump(exclude_unset=True) == {
        "description": "Best filter coffee in SBR",
        "area_id": None,
    }


@pytest.mark.parametrize(
    "field", ["name", "slug", "category", "geofence_radius_m", "first_notch_spots"]
)
def test_update_rejects_null_for_required_columns(field: str) -> None:
    with pytest.raises(ValidationError):
        VenueUpdate(**{field: None})


def test_update_needs_both_coordinates() -> None:
    with pytest.raises(ValidationError):
        VenueUpdate(latitude="23.0225")

    cleared = VenueUpdate(latitude=None, longitude=None)
    assert cleared.model_dump(exclude_unset=True) == {"latitude": None, "longitude": None}
