"""Aggregates all module routers. Register each new module's router here."""

from fastapi import APIRouter

from app.modules.audit.router import router as admin_audit_router
from app.modules.auth.admin_router import router as admin_auth_router
from app.modules.auth.router import router as auth_router
from app.modules.auth.venue_router import router as venue_auth_router
from app.modules.cards.router import public_router as public_cards_router
from app.modules.cards.router import router as cards_router
from app.modules.dropdowns.router import router as dropdowns_router
from app.modules.health.router import router as health_router
from app.modules.jobs.router import router as admin_jobs_router
from app.modules.notches.admin_router import router as admin_notches_router
from app.modules.passport.router import router as passport_router
from app.modules.site.router import router as site_router
from app.modules.users.admin_router import router as admin_users_router
from app.modules.venue_applications.admin_router import router as admin_applications_router
from app.modules.venue_applications.router import router as venue_applications_router
from app.modules.venues.admin_router import router as admin_venues_router
from app.modules.venues.counter_router import display_router, owner_router
from app.modules.venues.dashboard_router import router as venue_dashboard_router
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
api_router.include_router(owner_router)
api_router.include_router(display_router)
api_router.include_router(whatsapp_router)
api_router.include_router(whatsapp_dev_router)
# Customers (passport) and the public website
api_router.include_router(passport_router)
api_router.include_router(cards_router)
api_router.include_router(site_router)
api_router.include_router(public_cards_router)
# Venue app
api_router.include_router(venue_dashboard_router)
# Admin panel
api_router.include_router(admin_notches_router)
api_router.include_router(admin_venues_router)
api_router.include_router(admin_users_router)
api_router.include_router(admin_jobs_router)
api_router.include_router(admin_audit_router)
