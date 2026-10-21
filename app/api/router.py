"""Aggregates all module routers. Register each new module's router here."""

from fastapi import APIRouter

from app.modules.admin_users.roles_router import router as admin_roles_router
from app.modules.audit.router import router as audit_router
from app.modules.auth.admin_router import router as admin_auth_router
from app.modules.auth.router import router as auth_router
from app.modules.auth.venue_router import router as venue_auth_router
from app.modules.dropdowns.router import router as dropdowns_router
from app.modules.health.router import router as health_router
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
api_router.include_router(audit_router)
api_router.include_router(admin_roles_router)
api_router.include_router(owner_router)
api_router.include_router(display_router)
api_router.include_router(whatsapp_router)
api_router.include_router(whatsapp_dev_router)
