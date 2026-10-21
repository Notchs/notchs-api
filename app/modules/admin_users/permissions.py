"""The fixed list of admin permissions. Roles tick some of these; the Owner role has them all.

Every admin endpoint requires one (`RequirePermission` in auth/dependencies.py). A module that adds
admin endpoints adds its permissions here, with a group and a plain-language label for the roles
screen. Keys are stored in `admin_role_permissions.permission` (max 40 characters).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class Permission(StrEnum):
    DASHBOARD_VIEW = "dashboard.view"
    ADMINS_VIEW = "admins.view"
    ADMINS_MANAGE = "admins.manage"
    ROLES_MANAGE = "roles.manage"
    AUDIT_VIEW = "audit.view"
    APPLICATIONS_VIEW = "applications.view"
    APPLICATIONS_REVIEW = "applications.review"
    VENUES_VIEW = "venues.view"
    VENUES_EDIT = "venues.edit"
    VENUES_STATUS = "venues.status"
    VENUES_LOGIN = "venues.login"
    CUSTOMERS_VIEW = "customers.view"
    CUSTOMERS_REVEAL_PHONE = "customers.reveal_phone"
    CUSTOMERS_BLOCK = "customers.block"
    WHATSAPP_VIEW = "whatsapp.view"
    WHATSAPP_SIMULATE = "whatsapp.simulate"
    LOCATIONS_MANAGE = "locations.manage"
    BILLING_VIEW = "billing.view"
    BILLING_MANAGE = "billing.manage"
    SETTINGS_MANAGE = "settings.manage"
    ANNOUNCEMENTS_SEND = "announcements.send"


@dataclass(frozen=True, slots=True)
class PermissionInfo:
    key: Permission
    group: str
    label: str
    description: str


CATALOG: tuple[PermissionInfo, ...] = (
    PermissionInfo(
        Permission.DASHBOARD_VIEW, "Dashboard", "View the dashboard", "Overview numbers."
    ),
    PermissionInfo(Permission.ADMINS_VIEW, "Team", "View the team", "Admins and their roles."),
    PermissionInfo(
        Permission.ADMINS_MANAGE,
        "Team",
        "Manage the team",
        "Invite admins, change their role, deactivate them, end their sessions.",
    ),
    PermissionInfo(
        Permission.ROLES_MANAGE, "Team", "Manage roles", "Create, edit and delete roles."
    ),
    PermissionInfo(
        Permission.AUDIT_VIEW, "Audit log", "View the audit log", "Who did what, and when."
    ),
    PermissionInfo(
        Permission.APPLICATIONS_VIEW,
        "Venue applications",
        "View applications",
        "The queue of venues that applied.",
    ),
    PermissionInfo(
        Permission.APPLICATIONS_REVIEW,
        "Venue applications",
        "Approve and reject",
        "Approving creates the venue and its login.",
    ),
    PermissionInfo(Permission.VENUES_VIEW, "Venues", "View venues", "Venue list and details."),
    PermissionInfo(Permission.VENUES_EDIT, "Venues", "Edit venues", "Name, address, category..."),
    PermissionInfo(
        Permission.VENUES_STATUS, "Venues", "Change venue status", "Pause, close or reopen."
    ),
    PermissionInfo(
        Permission.VENUES_LOGIN,
        "Venues",
        "Manage venue logins",
        "Send a new set-password link, remove the counter phone.",
    ),
    PermissionInfo(
        Permission.CUSTOMERS_VIEW,
        "Customers",
        "View customers",
        "Customer list and profiles (phone numbers masked).",
    ),
    PermissionInfo(
        Permission.CUSTOMERS_REVEAL_PHONE,
        "Customers",
        "Show phone numbers",
        "Show a customer's full number. Every reveal is in the audit log.",
    ),
    PermissionInfo(
        Permission.CUSTOMERS_BLOCK, "Customers", "Block customers", "Block and unblock."
    ),
    PermissionInfo(
        Permission.WHATSAPP_VIEW,
        "WhatsApp",
        "View conversations",
        "Messages between customers and the bot.",
    ),
    PermissionInfo(
        Permission.WHATSAPP_SIMULATE,
        "WhatsApp",
        "Use the bot simulator",
        "Act as a customer (not in production). Replies can contain login links.",
    ),
    PermissionInfo(
        Permission.LOCATIONS_MANAGE,
        "Locations and lists",
        "Manage cities, areas and categories",
        "Add or change cities, areas and venue categories.",
    ),
    PermissionInfo(
        Permission.BILLING_VIEW, "Billing", "View billing", "Plans, subscriptions and invoices."
    ),
    PermissionInfo(
        Permission.BILLING_MANAGE,
        "Billing",
        "Manage billing",
        "Change plans and subscriptions, issue invoices, mark them paid.",
    ),
    PermissionInfo(
        Permission.SETTINGS_MANAGE,
        "Settings",
        "Change platform settings",
        "Scan limits, points rules, feature switches.",
    ),
    PermissionInfo(
        Permission.ANNOUNCEMENTS_SEND,
        "Announcements",
        "Send announcements",
        "Email all or some venues.",
    ),
)

ALL_PERMISSIONS: frozenset[Permission] = frozenset(Permission)

# What the built-in Reviewer role gets on first set-up: look, don't touch (and not the audit log
# or the team).
REVIEWER_PERMISSIONS: frozenset[Permission] = frozenset(
    p
    for p in Permission
    if p.value.endswith(".view") and p not in {Permission.AUDIT_VIEW, Permission.ADMINS_VIEW}
)

assert {info.key for info in CATALOG} == ALL_PERMISSIONS, "every permission needs a catalog entry"
assert all(len(p.value) <= 40 for p in Permission)


def known(keys: Iterable[str]) -> frozenset[Permission]:
    """The keys that are real permissions; anything else (removed from the code) is dropped."""
    values = {p.value for p in Permission}
    return frozenset(Permission(key) for key in keys if key in values)
