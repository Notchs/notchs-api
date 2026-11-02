from sqlalchemy import CheckConstraint, UniqueConstraint

from app.db.models import Base
from app.modules.venues.models import Venue, VenueCategory, VenueStatus

# Columns from the spec sheet, plus `venue_code` (docs/WORKPLAN.md §1.1) and `scan_code` (scans
# module step 1).
SPEC_COLUMNS = {
    "id": False,
    "venue_code": False,
    "scan_code": False,
    "city_id": False,
    "area_id": True,
    "name": False,
    "slug": False,
    "category": False,
    "address": True,
    "landmark": True,
    "latitude": True,
    "longitude": True,
    "geofence_radius_m": False,
    "description": True,
    "photo_s3_key": True,
    "opening_hours": True,
    "status": False,
    "founding_venue_no": True,
    "first_notch_spots": False,
    "free_until": True,
    "monthly_fee_paise": True,
    "signed_by_admin_id": True,
    "created_at": False,
    "updated_at": False,
    "deleted_at": True,
    # Not in the spec sheet: the running "Notch #" and the venue page's tags.
    "notch_count": False,
    "tags": False,
}


def test_all_tables_are_registered_for_alembic() -> None:
    assert {"cities", "areas", "admin_users", "venues"} <= set(Base.metadata.tables)


def test_venue_columns_and_nullability_match_the_spec() -> None:
    columns = {column.name: column.nullable for column in Venue.__table__.columns}

    assert columns == SPEC_COLUMNS


def test_venue_server_defaults_match_the_spec() -> None:
    table = Venue.__table__

    assert table.c.geofence_radius_m.server_default.arg == "150"
    assert table.c.first_notch_spots.server_default.arg == "50"
    assert table.c.status.server_default.arg == "pending"


def test_venue_foreign_keys() -> None:
    targets = {fk.parent.name: fk.target_fullname for fk in Venue.__table__.foreign_keys}

    assert targets == {
        "city_id": "cities.id",
        "area_id": "areas.id",
        "signed_by_admin_id": "admin_users.id",
    }


def test_venue_indexes_and_unique_constraints() -> None:
    table = Venue.__table__
    indexes = {index.name for index in table.indexes}
    uniques = {
        constraint.name: [column.name for column in constraint.columns]
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert indexes == {
        "ix_venues_city_id",
        "ix_venues_area_id",
        "ix_venues_category",
        "ix_venues_status",
    }
    assert uniques == {
        "uq_venues_city_id_slug": ["city_id", "slug"],
        "uq_venues_venue_code": ["venue_code"],
        "uq_venues_scan_code": ["scan_code"],
        "uq_venues_founding_venue_no": ["founding_venue_no"],
    }


def test_venue_checks_list_every_enum_value() -> None:
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in Venue.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    for category in VenueCategory:
        assert f"'{category.value}'" in checks["ck_venues_category_valid"]
    for status in VenueStatus:
        assert f"'{status.value}'" in checks["ck_venues_status_valid"]
    assert "BETWEEN 1 AND 30" in checks["ck_venues_founding_venue_no_range"]


def test_enum_values_fit_their_columns() -> None:
    table = Venue.__table__

    assert max(len(category.value) for category in VenueCategory) <= table.c.category.type.length
    assert max(len(status.value) for status in VenueStatus) <= table.c.status.type.length
