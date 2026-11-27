"""The public map's GeoJSON, no database (endpoint: tests/integration/test_venue_map.py)."""

from decimal import Decimal
from types import SimpleNamespace

from app.modules.locations.models import City
from app.modules.venues.map import lng_lat, to_feature, to_map_city
from app.modules.venues.map_schemas import MapVenueCollection


def _row(**overrides) -> SimpleNamespace:
    data = {
        "venue_code": "VEN-7K3M9QX2",
        "name": "Kahwa House",
        "slug": "kahwa-house",
        "category": "cafe",
        "latitude": Decimal("23.033863"),
        "longitude": Decimal("72.585022"),
        "area": "SBR",
    }
    return SimpleNamespace(**(data | overrides))


def test_points_are_longitude_first_as_geojson_wants() -> None:
    feature = to_feature(_row(), None)
    assert feature.geometry.coordinates == (72.585022, 23.033863)


def test_a_feature_carries_only_what_a_pin_needs() -> None:
    dumped = to_feature(_row(), 3).model_dump()
    assert dumped == {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": (72.585022, 23.033863)},
        "properties": {
            "venue_code": "VEN-7K3M9QX2",
            "name": "Kahwa House",
            "slug": "kahwa-house",
            "category": "cafe",
            "area": "SBR",
            "rank": 3,
        },
    }


def test_a_place_without_an_area_still_shows() -> None:
    assert to_feature(_row(area=None), None).properties.area is None


def test_the_collection_is_a_geojson_feature_collection() -> None:
    collection = MapVenueCollection(features=[to_feature(_row(), None)]).model_dump(mode="json")
    assert collection["type"] == "FeatureCollection"
    assert collection["features"][0]["geometry"]["coordinates"] == [72.585022, 23.033863]


def test_city_centre_is_none_until_the_city_has_coordinates() -> None:
    city = City(name="Ahmedabad", slug="ahmedabad")
    assert to_map_city(city).center is None
    city.latitude, city.longitude = Decimal("23.022505"), Decimal("72.571365")
    assert to_map_city(city).center == (72.571365, 23.022505)


def test_half_a_coordinate_is_no_coordinate() -> None:
    assert lng_lat(Decimal("23.0"), None) is None
