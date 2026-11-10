"""Imports every module's dropdown lists so they are registered. Add new modules here.

Like app/db/models.py does for models: a list that is never imported is never registered.
"""

from app.modules.locations import dropdowns as locations_dropdowns
from app.modules.tags import dropdowns as tags_dropdowns
from app.modules.venues import dropdowns as venues_dropdowns

__all__ = ["locations_dropdowns", "tags_dropdowns", "venues_dropdowns"]
