"""Niches: the kind of place a venue is (its `category`). Each niche has its own colour of
points, and a customer gets at most one Notch per niche per day (team decisions, Q21 and Q24).

Colours for cafés (blue), restaurants (violet) and turfs (green) are decided; the others are
not yet. Every niche is worth the same until the team sets the values (after the first MVP).
"""

from dataclasses import dataclass

from app.modules.venues.models import VenueCategory


@dataclass(frozen=True, slots=True)
class Niche:
    # How a customer reads it in a sentence: "today's café Notch".
    label: str
    colour: str
    visit_points: int


DEFAULT_VISIT_POINTS = 10

NICHES: dict[str, Niche] = {
    VenueCategory.CAFE: Niche("café", "blue", DEFAULT_VISIT_POINTS),
    VenueCategory.RESTAURANT: Niche("restaurant", "violet", DEFAULT_VISIT_POINTS),
    VenueCategory.TURF_GYM: Niche("turf or gym", "green", DEFAULT_VISIT_POINTS),
    # Not decided yet.
    VenueCategory.BAR: Niche("bar", "grey", DEFAULT_VISIT_POINTS),
    VenueCategory.HOTEL: Niche("hotel", "grey", DEFAULT_VISIT_POINTS),
    VenueCategory.OTHER: Niche("place", "grey", DEFAULT_VISIT_POINTS),
}


def niche_of(category: str) -> Niche:
    return NICHES[VenueCategory(category)]
