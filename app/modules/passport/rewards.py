"""The monthly reward (team decision, Q20): a customer who visits 5 new cafés in a month can get
free food worth up to ₹249 at a Notchs place.

"New" means a café they had never been notched at before: the visit is their first there. How
the food is claimed is not decided yet, so this only says how far along a customer is.
"""

from app.modules.venues.models import VenueCategory

NEW_CAFES_NEEDED = 5
REWARD_CATEGORY = VenueCategory.CAFE
REWARD_VALUE_RUPEES = 249
