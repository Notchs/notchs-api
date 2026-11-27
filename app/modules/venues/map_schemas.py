"""`GET /venues/map`: a city's live places as GeoJSON, ready to use as a MapLibre source."""

from typing import Literal

from app.common.schemas import BaseSchema

# GeoJSON order: [longitude, latitude].
LngLat = tuple[float, float]


class MapPoint(BaseSchema):
    type: Literal["Point"] = "Point"
    coordinates: LngLat


class MapVenueProperties(BaseSchema):
    """Only what a pin and its popup need. Nothing about owners, billing or customers."""

    venue_code: str
    name: str
    # With the city's slug, the venue page: /<city>/v/<slug>
    slug: str
    category: str
    area: str | None


class MapVenueFeature(BaseSchema):
    type: Literal["Feature"] = "Feature"
    geometry: MapPoint
    properties: MapVenueProperties


class MapVenueCollection(BaseSchema):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[MapVenueFeature]


class MapCity(BaseSchema):
    slug: str
    name: str
    # Where to centre the map first; None if the city has no coordinates yet.
    center: LngLat | None


class VenueMapOut(BaseSchema):
    city: MapCity
    venues: MapVenueCollection
