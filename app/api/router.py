"""Aggregates all module routers. Register each new module's router here."""

from fastapi import APIRouter

from app.modules.auth.admin_router import router as admin_auth_router
from app.modules.auth.router import router as auth_router
from app.modules.auth.venue_router import router as venue_auth_router
from app.modules.dropdowns.router import router as dropdowns_router
from app.modules.health.router import router as health_router
from app.modules.leaderboards.router import router as leaderboards_router
from app.modules.passport.router import router as passport_router
from app.modules.tags.router import admin_router as admin_tags_router
from app.modules.tags.router import venue_router as venue_tags_router
from app.modules.venue_applications.admin_router import router as admin_applications_router
from app.modules.venue_applications.router import router as venue_applications_router
from app.modules.venues.counter_router import display_router, owner_router
from app.modules.whatsapp.dev_router import router as whatsapp_dev_router
from app.modules.whatsapp.router import router as whatsapp_router

api_router = APIRouter()

api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(venue_auth_router)
api_router.include_router(venue_applications_router)
api_router.include_router(dropdowns_router)
api_router.include_router(admin_auth_router)
api_router.include_router(admin_applications_router)
api_router.include_router(leaderboards_router)
api_router.include_router(passport_router)
api_router.include_router(admin_tags_router)
api_router.include_router(venue_tags_router)
api_router.include_router(owner_router)
api_router.include_router(display_router)
api_router.include_router(whatsapp_router)
api_router.include_router(whatsapp_dev_router)
