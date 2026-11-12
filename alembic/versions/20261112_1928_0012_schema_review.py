"""schema review

Every change agreed in the table-by-table review of 12 Nov 2026, in one migration. Rule agreed: a
record's state is a `status` column (text + CHECK), never an `is_active` flag.

- cities: `status` (live, coming_soon, hidden) replaces is_active; map position, launch date,
  order, country, image.
- areas: `status`, map position, source + external id (OpenStreetMap imports), aliases,
  PIN codes, order.
- admin_users: `status` (invited, active, disabled) replaces is_active; roles become data
  (`admin_roles`, `admin_role_permissions`, from the paused admin module 26a99e7) with two
  built-in roles, "Admin" (is_owner) and "Reviewer"; two-factor columns; who invited or disabled
  them; phone (team decision).
- audit_logs: append-only, partitioned by month (from 26a99e7).
- auth_tokens: `revoke_reason`.
- users: `blocked_at`, `blocked_reason`. New `consents` table with the exact wording shown.
- venues: signed / live / closed dates, status reason, PIN code, Google place id, price range,
  logo. New `venue_photos`.
- venue_members: `status` replaces is_active; password changed date, disabled date, inviter,
  two-factor columns.
- counter_devices: `status` (pending_pairing, active, revoked) replaces is_active; why and by
  whom it was removed; app version, device model, push token.
- venue_applications: area, area suggestion, PIN code, Instagram, where they heard of us, team
  notes; statuses `needs_info` and `withdrawn`.
- scan_sessions: reject reason `too_many_attempts`, `source`, `code_age_s`.
- visits: who, when and why a Notch was removed; `niche` fixed at the time of the visit.
  New `visit_feedback`.
- points_ledger: admin and note on adjustments, `city_id`, one row per visit per reason.
- visit_claims: `expired` status, reject codes, bill details, photo hash, notes.
- referrals: `status`, qualifying visit, source. star_points_ledger: `referral_reversed`,
  admin and note on adjustments.
- tags: `status`, order, emoji, aliases, categories, venue suggestions. venue_tags: primary tag.
  venue_items: price, signature, photo, updated_at. visit_tags: liked.
- whatsapp_messages: reply link, Meta's error title, delivered / read times, and a CHECK that
  refuses paid template messages.

Backfills use real rows only. Nothing is made up: past disables, blocks and removals have no
date, and their reason says "Not recorded (before migration 0012)". A duplicate that a new
unique rule would forbid stops the migration (fix the data, never hide it).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-12 19:28:57.946767

"""

import hashlib
from collections.abc import Iterator, Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.common.ids import new_code, new_uuid
from app.core.config import settings

revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOT_RECORDED = "Not recorded (before migration 0012)"
APP_ROLE = "notchs_app"
AUDIT_FIRST_MONTH = date(2026, 11, 1)
AUDIT_LAST_MONTH = date(2027, 12, 1)

# Frozen here on purpose: the only consent version shown so far (users/consent.py), so old
# consents can be stored with their exact words. Later wording changes must not change this.
CONSENT_VERSION = "draft-2026-11"


def _consent_text_draft_2026_11() -> str:
    return (
        "We use your WhatsApp number and name to keep your stamps, points and rank. Your profile "
        f"is private unless you make it public. Privacy notice: {settings.CUSTOMER_APP_URL}/privacy"
        "\n\nTap below to confirm you agree and are 18 or over."
    )


CATEGORIES = ('cafe', 'restaurant', 'bar', 'hotel', 'turf_gym', 'theatre', 'other')


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(f"'{v}'" for v in values)})"


def _check(table: str, name: str, condition: str) -> None:
    op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, condition)


def _drop_check(table: str, name: str) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")


def _coordinates(table: str) -> None:
    _check(table, "latitude_range", "latitude BETWEEN -90 AND 90")
    _check(table, "longitude_range", "longitude BETWEEN -180 AND 180")
    _check(table, "coordinates_paired", "(latitude IS NULL) = (longitude IS NULL)")


def _months() -> Iterator[tuple[date, date]]:
    start = AUDIT_FIRST_MONTH
    while start <= AUDIT_LAST_MONTH:
        end = date(start.year + start.month // 12, start.month % 12 + 1, 1)
        yield start, end
        start = end


def _fail_on_duplicates(sql: str, what: str) -> None:
    found = op.get_bind().execute(sa.text(sql)).scalar()
    if found:
        raise RuntimeError(f"Migration 0012 stopped: {found} duplicate {what}. Fix the data first.")


def upgrade() -> None:
    _cities_and_areas()
    _admins_and_audit()
    _auth_tokens()
    _users_and_consents()
    _venues()
    _venue_members_and_counters()
    _venue_applications()
    _scans_visits_points()
    _claims()
    _referrals_and_star_points()
    _tags()
    _whatsapp()


# --- cities, areas ----------------------------------------------------------------------------


def _cities_and_areas() -> None:
    op.add_column('cities', sa.Column('status', sa.String(length=12), server_default='hidden', nullable=False))
    op.execute("UPDATE cities SET status = CASE WHEN is_active THEN 'live' ELSE 'hidden' END")
    op.drop_column('cities', 'is_active')
    op.add_column('cities', sa.Column('latitude', sa.Numeric(precision=9, scale=6), nullable=True))
    op.add_column('cities', sa.Column('longitude', sa.Numeric(precision=9, scale=6), nullable=True))
    op.add_column('cities', sa.Column('launched_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('cities', sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False))
    op.add_column('cities', sa.Column('country', sa.String(length=2), server_default='IN', nullable=False))
    op.add_column('cities', sa.Column('image_s3_key', sa.String(length=255), nullable=True))
    op.create_index(op.f('ix_cities_status'), 'cities', ['status'], unique=False)
    _check('cities', 'status_valid', _in('status', ('live', 'coming_soon', 'hidden')))
    _check('cities', 'country_format', "country ~ '^[A-Z]{2}$'")
    _coordinates('cities')

    op.add_column('areas', sa.Column('status', sa.String(length=8), server_default='live', nullable=False))
    op.add_column('areas', sa.Column('latitude', sa.Numeric(precision=9, scale=6), nullable=True))
    op.add_column('areas', sa.Column('longitude', sa.Numeric(precision=9, scale=6), nullable=True))
    op.add_column('areas', sa.Column('source', sa.String(length=12), server_default='manual', nullable=False))
    op.add_column('areas', sa.Column('external_id', sa.Text(), nullable=True))
    op.add_column('areas', sa.Column('aliases', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False))
    op.add_column('areas', sa.Column('pincodes', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False))
    op.add_column('areas', sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False))
    op.create_unique_constraint('uq_areas_source_external_id', 'areas', ['source', 'external_id'])
    _check('areas', 'status_valid', _in('status', ('live', 'hidden')))
    _check('areas', 'source_valid', _in('source', ('osm', 'india_post', 'google', 'manual')))
    _check('areas', 'pincodes_format', "array_to_string(pincodes, ',') ~ '^([0-9]{6}(,[0-9]{6})*)?$'")
    _coordinates('areas')


# --- admins, roles, audit log -----------------------------------------------------------------


def _admins_and_audit() -> None:
    op.create_table('admin_roles',
    sa.Column('admin_role_code', sa.String(length=12), nullable=False),
    sa.Column('name', sa.String(length=40), nullable=False),
    sa.Column('description', sa.String(length=200), nullable=True),
    sa.Column('is_owner', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_admin_roles')),
    sa.UniqueConstraint('admin_role_code', name=op.f('uq_admin_roles_admin_role_code'))
    )
    op.create_index('uq_admin_roles_name_lower', 'admin_roles', [sa.literal_column('lower(name)')], unique=True)
    op.create_index('uq_admin_roles_owner', 'admin_roles', ['is_owner'], unique=True, postgresql_where=sa.text('is_owner'))
    op.create_table('admin_role_permissions',
    sa.Column('role_id', sa.Uuid(), nullable=False),
    sa.Column('permission', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['role_id'], ['admin_roles.id'], name=op.f('fk_admin_role_permissions_role_id_admin_roles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('role_id', 'permission', name=op.f('pk_admin_role_permissions'))
    )

    # The two built-in roles; existing admins keep the access they had.
    admin_id, reviewer_id = new_uuid(), new_uuid()
    roles = sa.table(
        "admin_roles",
        sa.column("id", sa.Uuid()),
        sa.column("admin_role_code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("is_owner", sa.Boolean()),
    )
    op.bulk_insert(roles, [
        {
            "id": admin_id,
            "admin_role_code": new_code("ROL"),
            "name": "Admin",
            "description": "Everything, including permissions added later. Cannot be changed.",
            "is_owner": True,
        },
        {
            "id": reviewer_id,
            "admin_role_code": new_code("ROL"),
            "name": "Reviewer",
            "description": "Reviews proof and looks around; cannot change settings or the team.",
            "is_owner": False,
        },
    ])
    op.add_column('admin_users', sa.Column('role_id', sa.Uuid(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE admin_users SET role_id = CASE WHEN role = 'admin' THEN :admin ELSE :reviewer END"
        ).bindparams(
            sa.bindparam("admin", admin_id, type_=sa.Uuid()),
            sa.bindparam("reviewer", reviewer_id, type_=sa.Uuid()),
        )
    )
    op.alter_column('admin_users', 'role_id', nullable=False)
    op.create_index(op.f('ix_admin_users_role_id'), 'admin_users', ['role_id'], unique=False)
    op.create_foreign_key(op.f('fk_admin_users_role_id_admin_roles'), 'admin_users', 'admin_roles', ['role_id'], ['id'], ondelete='RESTRICT')
    op.drop_column('admin_users', 'role')  # its CHECK goes with it

    op.add_column('admin_users', sa.Column('status', sa.String(length=10), server_default='invited', nullable=False))
    op.execute(
        "UPDATE admin_users SET status = CASE "
        "WHEN NOT is_active THEN 'disabled' "
        "WHEN password_hash IS NOT NULL THEN 'active' ELSE 'invited' END"
    )
    op.drop_column('admin_users', 'is_active')
    op.add_column('admin_users', sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('admin_users', sa.Column('mfa_secret_enc', sa.String(length=255), nullable=True))
    op.add_column('admin_users', sa.Column('mfa_enabled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('admin_users', sa.Column('invited_by_admin_id', sa.Uuid(), nullable=True))
    op.add_column('admin_users', sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('admin_users', sa.Column('disabled_by_admin_id', sa.Uuid(), nullable=True))
    op.add_column('admin_users', sa.Column('phone_e164', sa.String(length=16), nullable=True))
    op.create_foreign_key(op.f('fk_admin_users_invited_by_admin_id_admin_users'), 'admin_users', 'admin_users', ['invited_by_admin_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key(op.f('fk_admin_users_disabled_by_admin_id_admin_users'), 'admin_users', 'admin_users', ['disabled_by_admin_id'], ['id'], ondelete='SET NULL')
    _check('admin_users', 'status_valid', _in('status', ('invited', 'active', 'disabled')))
    _check('admin_users', 'disabled_at_only_when_disabled', "status = 'disabled' OR disabled_at IS NULL")
    _check('admin_users', 'active_has_password', "status <> 'active' OR password_hash IS NOT NULL")
    _check('admin_users', 'mfa_has_secret', "(mfa_enabled_at IS NULL) OR (mfa_secret_enc IS NOT NULL)")

    op.create_table('audit_logs',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('actor_type', sa.String(length=14), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=True),
    sa.Column('action', sa.String(length=40), nullable=False),
    sa.Column('entity_type', sa.String(length=30), nullable=False),
    sa.Column('entity_id', sa.String(length=40), nullable=True),
    sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.Column('request_id', sa.String(length=32), nullable=True),
    sa.CheckConstraint("actor_type IN ('user', 'venue_member', 'admin', 'device', 'system', 'public')", name=op.f('ck_audit_logs_actor_type_valid')),
    sa.PrimaryKeyConstraint('id', 'created_at', name=op.f('pk_audit_logs')),
    postgresql_partition_by='RANGE (created_at)'
    )
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index('ix_audit_logs_actor', 'audit_logs', ['actor_type', 'actor_id'], unique=False)
    op.create_index(op.f('ix_audit_logs_created_at'), 'audit_logs', ['created_at'], unique=False)
    op.create_index('ix_audit_logs_entity', 'audit_logs', ['entity_type', 'entity_id'], unique=False)
    # Monthly partitions, plus a default one so an insert never fails if the partition job is
    # late. That job must exist before 2028.
    tables = ["audit_logs"]
    for start, end in _months():
        name = f"audit_logs_{start:%Y_%m}"
        op.execute(
            f"CREATE TABLE {name} PARTITION OF audit_logs "
            f"FOR VALUES FROM ('{start:%Y-%m-%d} 00:00:00+00') TO ('{end:%Y-%m-%d} 00:00:00+00')"
        )
        tables.append(name)
    op.execute("CREATE TABLE audit_logs_default PARTITION OF audit_logs DEFAULT")
    tables.append("audit_logs_default")
    # Append-only for the app: no UPDATE, DELETE or TRUNCATE, when that role exists.
    revokes = "\n".join(
        f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM {APP_ROLE};" for table in tables
    )
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN\n"
        f"{revokes}\nEND IF; END $$"
    )


# --- auth tokens ------------------------------------------------------------------------------


def _auth_tokens() -> None:
    op.add_column('auth_tokens', sa.Column('revoke_reason', sa.String(length=20), nullable=True))
    _check('auth_tokens', 'revoke_reason_valid', _in('revoke_reason', (
        'logout', 'superseded', 'reuse_detected', 'password_changed', 'account_disabled', 'admin',
    )))
    _check('auth_tokens', 'revoke_reason_when_revoked', "revoke_reason IS NULL OR revoked_at IS NOT NULL")


# --- users, consents --------------------------------------------------------------------------


def _users_and_consents() -> None:
    op.add_column('users', sa.Column('blocked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('blocked_reason', sa.String(length=200), nullable=True))
    op.execute(
        sa.text("UPDATE users SET blocked_reason = :reason WHERE status = 'blocked'").bindparams(
            reason=NOT_RECORDED
        )
    )
    _check('users', 'blocked_reason_when_blocked', "(status = 'blocked') = (blocked_reason IS NOT NULL)")
    _check('users', 'blocked_at_only_when_blocked', "status = 'blocked' OR blocked_at IS NULL")

    op.create_table('consents',
    sa.Column('subject_type', sa.String(length=20), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('venue_application_id', sa.Uuid(), nullable=True),
    sa.Column('purpose', sa.String(length=30), nullable=False),
    sa.Column('version', sa.String(length=20), nullable=False),
    sa.Column('wording', sa.Text(), nullable=False),
    sa.Column('wording_sha256', sa.String(length=64), nullable=False),
    sa.Column('channel', sa.String(length=10), nullable=False),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('withdrawn_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("((subject_type = 'user') = (user_id IS NOT NULL)) AND ((subject_type = 'venue_application') = (venue_application_id IS NOT NULL))", name=op.f('ck_consents_subject_matches_type')),
    sa.CheckConstraint("channel IN ('whatsapp', 'website')", name=op.f('ck_consents_channel_valid')),
    sa.CheckConstraint("purpose IN ('privacy_notice', 'venue_application_privacy')", name=op.f('ck_consents_purpose_valid')),
    sa.CheckConstraint("subject_type IN ('user', 'venue_application')", name=op.f('ck_consents_subject_type_valid')),
    sa.CheckConstraint("wording_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_consents_wording_sha256_format')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_consents_user_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['venue_application_id'], ['venue_applications.id'], name=op.f('fk_consents_venue_application_id_venue_applications'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_consents'))
    )
    op.create_index('ix_consents_user_purpose_accepted_at', 'consents', ['user_id', 'purpose', 'accepted_at'], unique=False)
    op.create_index('ix_consents_venue_application_id', 'consents', ['venue_application_id'], unique=False)
    # Consents already given (all on WhatsApp, all the one draft version so far).
    wording = _consent_text_draft_2026_11()
    op.execute(
        sa.text(
            "INSERT INTO consents (id, subject_type, user_id, purpose, version, wording, "
            "wording_sha256, channel, accepted_at) "
            "SELECT uuidv7(), 'user', id, 'privacy_notice', consent_version, :wording, :sha, "
            "'whatsapp', consent_at FROM users "
            "WHERE consent_at IS NOT NULL AND consent_version = :version"
        ).bindparams(
            wording=wording,
            sha=hashlib.sha256(wording.encode("utf-8")).hexdigest(),
            version=CONSENT_VERSION,
        )
    )
    _fail_on_duplicates(
        "SELECT count(*) FROM users WHERE consent_at IS NOT NULL "
        f"AND consent_version IS DISTINCT FROM '{CONSENT_VERSION}'",
        "consents of an unknown version (their wording is not in this migration)",
    )


# --- venues -----------------------------------------------------------------------------------


def _venues() -> None:
    op.add_column('venues', sa.Column('signed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('venues', sa.Column('live_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('venues', sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('venues', sa.Column('status_reason', sa.String(length=200), nullable=True))
    op.add_column('venues', sa.Column('pincode', sa.String(length=6), nullable=True))
    op.add_column('venues', sa.Column('google_place_id', sa.String(length=255), nullable=True))
    op.add_column('venues', sa.Column('price_range', sa.SmallInteger(), nullable=True))
    op.add_column('venues', sa.Column('logo_s3_key', sa.String(length=255), nullable=True))
    op.create_index('uq_venues_google_place_id', 'venues', ['google_place_id'], unique=True, postgresql_where=sa.text('google_place_id IS NOT NULL'))
    _check('venues', 'pincode_format', "pincode ~ '^[0-9]{6}$'")
    _check('venues', 'price_range_valid', 'price_range BETWEEN 1 AND 4')
    # Known dates only: signed when its application was approved, live when a counter first
    # paired (for venues that went live).
    op.execute(
        "UPDATE venues SET signed_at = a.reviewed_at FROM venue_applications a "
        "WHERE a.venue_id = venues.id AND a.status = 'approved'"
    )
    op.execute(
        "UPDATE venues SET live_at = c.first_paired FROM ("
        "  SELECT venue_id, min(paired_at) AS first_paired FROM counter_devices"
        "  WHERE paired_at IS NOT NULL GROUP BY venue_id"
        ") c WHERE c.venue_id = venues.id AND venues.status IN ('counter_live', 'paused', 'closed')"
    )

    op.create_table('venue_photos',
    sa.Column('venue_id', sa.Uuid(), nullable=False),
    sa.Column('s3_key', sa.String(length=255), nullable=False),
    sa.Column('caption', sa.String(length=120), nullable=True),
    sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['venue_id'], ['venues.id'], name=op.f('fk_venue_photos_venue_id_venues'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_venue_photos'))
    )
    op.create_index('ix_venue_photos_venue_id_sort_order', 'venue_photos', ['venue_id', 'sort_order'], unique=False)


# --- venue members, counter phones ------------------------------------------------------------


def _venue_members_and_counters() -> None:
    op.add_column('venue_members', sa.Column('status', sa.String(length=10), server_default='invited', nullable=False))
    op.execute(
        "UPDATE venue_members SET status = CASE "
        "WHEN NOT is_active THEN 'disabled' "
        "WHEN password_hash IS NOT NULL THEN 'active' ELSE 'invited' END"
    )
    op.drop_column('venue_members', 'is_active')
    op.add_column('venue_members', sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('venue_members', sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('venue_members', sa.Column('invited_by_member_id', sa.Uuid(), nullable=True))
    op.add_column('venue_members', sa.Column('mfa_secret_enc', sa.String(length=255), nullable=True))
    op.add_column('venue_members', sa.Column('mfa_enabled_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(op.f('fk_venue_members_invited_by_member_id_venue_members'), 'venue_members', 'venue_members', ['invited_by_member_id'], ['id'], ondelete='SET NULL')
    _check('venue_members', 'status_valid', _in('status', ('invited', 'active', 'disabled')))
    _check('venue_members', 'disabled_at_only_when_disabled', "status = 'disabled' OR disabled_at IS NULL")
    _check('venue_members', 'active_has_password', "status <> 'active' OR password_hash IS NOT NULL")
    _check('venue_members', 'mfa_has_secret', "(mfa_enabled_at IS NULL) OR (mfa_secret_enc IS NOT NULL)")

    op.add_column('counter_devices', sa.Column('status', sa.String(length=16), server_default='pending_pairing', nullable=False))
    op.execute(
        "UPDATE counter_devices SET status = CASE "
        "WHEN revoked_at IS NOT NULL THEN 'revoked' "
        "WHEN is_active AND token_hash IS NOT NULL THEN 'active' ELSE 'pending_pairing' END"
    )
    op.drop_index(op.f('uq_counter_devices_active_venue'), table_name='counter_devices', postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_column('counter_devices', 'is_active')
    op.create_index('uq_counter_devices_active_venue', 'counter_devices', ['venue_id'], unique=True, postgresql_where=sa.text("status <> 'revoked'"))
    op.add_column('counter_devices', sa.Column('revoke_reason', sa.String(length=16), nullable=True))
    op.add_column('counter_devices', sa.Column('revoked_by_member_id', sa.Uuid(), nullable=True))
    op.add_column('counter_devices', sa.Column('app_version', sa.String(length=20), nullable=True))
    op.add_column('counter_devices', sa.Column('device_model', sa.String(length=80), nullable=True))
    op.add_column('counter_devices', sa.Column('push_token', sa.String(length=255), nullable=True))
    op.create_foreign_key(op.f('fk_counter_devices_revoked_by_member_id_venue_members'), 'counter_devices', 'venue_members', ['revoked_by_member_id'], ['id'], ondelete='SET NULL')
    _check('counter_devices', 'status_valid', _in('status', ('pending_pairing', 'active', 'revoked')))
    _check('counter_devices', 'revoke_reason_valid', _in('revoke_reason', (
        'removed_by_venue', 'lost', 'replaced', 'venue_closed', 'admin',
    )))
    _check('counter_devices', 'revoked_at_when_revoked', "(status = 'revoked') = (revoked_at IS NOT NULL)")
    _check('counter_devices', 'revoke_reason_when_revoked', "revoke_reason IS NULL OR revoked_at IS NOT NULL")
    _check('counter_devices', 'active_is_paired', "status <> 'active' OR token_hash IS NOT NULL")


# --- venue applications -----------------------------------------------------------------------


def _venue_applications() -> None:
    op.add_column('venue_applications', sa.Column('area_id', sa.Uuid(), nullable=True))
    op.add_column('venue_applications', sa.Column('area_suggestion', sa.String(length=120), nullable=True))
    op.add_column('venue_applications', sa.Column('pincode', sa.String(length=6), nullable=True))
    op.add_column('venue_applications', sa.Column('instagram_handle', sa.String(length=30), nullable=True))
    op.add_column('venue_applications', sa.Column('heard_from', sa.String(length=12), nullable=True))
    op.add_column('venue_applications', sa.Column('admin_notes', sa.Text(), nullable=True))
    op.add_column('venue_applications', sa.Column('needs_info_message', sa.String(length=255), nullable=True))
    op.create_foreign_key(op.f('fk_venue_applications_area_id_areas'), 'venue_applications', 'areas', ['area_id'], ['id'], ondelete='SET NULL')
    op.drop_index(op.f('uq_venue_applications_pending_email'), table_name='venue_applications', postgresql_where="((status)::text = 'pending'::text)")
    op.create_index('uq_venue_applications_open_email', 'venue_applications', ['email'], unique=True, postgresql_where=sa.text("status IN ('pending', 'needs_info')"))
    _drop_check('venue_applications', 'status_valid')
    _check('venue_applications', 'status_valid', _in('status', (
        'pending', 'needs_info', 'approved', 'rejected', 'withdrawn',
    )))
    _check('venue_applications', 'heard_from_valid', _in('heard_from', (
        'instagram', 'friend', 'notchs_team', 'customer', 'other',
    )))
    _check('venue_applications', 'pincode_format', "pincode ~ '^[0-9]{6}$'")
    _check('venue_applications', 'instagram_handle_format', "instagram_handle ~ '^[a-z0-9._]{1,30}$'")


# --- scans, visits, points --------------------------------------------------------------------


def _scans_visits_points() -> None:
    op.add_column('scan_sessions', sa.Column('source', sa.String(length=12), server_default='code', nullable=False))
    op.add_column('scan_sessions', sa.Column('code_age_s', sa.Integer(), nullable=True))
    # An approved bill claim's scan is the photo's message, with no code.
    op.execute(
        "UPDATE scan_sessions SET source = 'bill_claim' WHERE inbound_message_id IN ("
        "  SELECT photo_message_id FROM visit_claims"
        "  WHERE status = 'approved' AND photo_message_id IS NOT NULL)"
    )
    _drop_check('scan_sessions', 'reject_reason_valid')
    _check('scan_sessions', 'reject_reason_valid', _in('reject_reason', (
        'unknown_venue', 'venue_unavailable', 'wrong_code', 'expired_code', 'code_used',
        'already_today', 'daily_limit', 'blocked_customer', 'too_many_attempts',
    )))
    _check('scan_sessions', 'source_valid', _in('source', ('code', 'bill_claim')))
    _check('scan_sessions', 'code_age_non_negative', 'code_age_s >= 0')

    op.add_column('visits', sa.Column('niche', sa.String(length=12), nullable=True))
    op.execute("UPDATE visits SET niche = v.category FROM venues v WHERE v.id = visits.venue_id")
    op.alter_column('visits', 'niche', nullable=False)
    op.add_column('visits', sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('visits', sa.Column('revoked_reason', sa.String(length=255), nullable=True))
    op.add_column('visits', sa.Column('revoked_by_admin_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f('fk_visits_revoked_by_admin_id_admin_users'), 'visits', 'admin_users', ['revoked_by_admin_id'], ['id'], ondelete='SET NULL')
    op.execute(
        sa.text("UPDATE visits SET revoked_reason = :reason WHERE status = 'revoked'").bindparams(
            reason=NOT_RECORDED
        )
    )
    _check('visits', 'niche_valid', _in('niche', CATEGORIES))
    _check('visits', 'revoked_reason_when_revoked', "(status = 'revoked') = (revoked_reason IS NOT NULL)")
    _check('visits', 'revoked_at_only_when_revoked', "status = 'revoked' OR (revoked_at IS NULL AND revoked_by_admin_id IS NULL)")

    op.create_table('visit_feedback',
    sa.Column('visit_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('venue_id', sa.Uuid(), nullable=False),
    sa.Column('rating', sa.SmallInteger(), nullable=False),
    sa.Column('comment', sa.String(length=500), nullable=True),
    sa.Column('status', sa.String(length=8), server_default='visible', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('visible', 'hidden')", name=op.f('ck_visit_feedback_status_valid')),
    sa.CheckConstraint('rating BETWEEN 1 AND 5', name=op.f('ck_visit_feedback_rating_range')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_visit_feedback_user_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['venue_id'], ['venues.id'], name=op.f('fk_visit_feedback_venue_id_venues'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['visit_id'], ['visits.id'], name=op.f('fk_visit_feedback_visit_id_visits'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_visit_feedback')),
    sa.UniqueConstraint('visit_id', name=op.f('uq_visit_feedback_visit_id'))
    )
    op.create_index(op.f('ix_visit_feedback_user_id'), 'visit_feedback', ['user_id'], unique=False)
    op.create_index('ix_visit_feedback_venue_created_at', 'visit_feedback', ['venue_id', 'created_at'], unique=False)

    op.add_column('points_ledger', sa.Column('admin_user_id', sa.Uuid(), nullable=True))
    op.add_column('points_ledger', sa.Column('note', sa.String(length=255), nullable=True))
    op.add_column('points_ledger', sa.Column('city_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_points_ledger_city_id'), 'points_ledger', ['city_id'], unique=False)
    op.create_foreign_key(op.f('fk_points_ledger_admin_user_id_admin_users'), 'points_ledger', 'admin_users', ['admin_user_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key(op.f('fk_points_ledger_city_id_cities'), 'points_ledger', 'cities', ['city_id'], ['id'], ondelete='SET NULL')
    # The city each row already counted for on the leaderboards (the visit's venue, else the
    # customer's home city), now fixed on the row.
    op.execute(
        "UPDATE points_ledger SET city_id = ve.city_id FROM visits vi JOIN venues ve "
        "ON ve.id = vi.venue_id WHERE vi.id = points_ledger.visit_id"
    )
    op.execute(
        "UPDATE points_ledger SET city_id = u.city_id FROM users u "
        "WHERE points_ledger.visit_id IS NULL AND u.id = points_ledger.user_id"
    )
    op.execute(
        sa.text("UPDATE points_ledger SET note = :note WHERE reason = 'adjustment'").bindparams(
            note=NOT_RECORDED
        )
    )
    _fail_on_duplicates(
        "SELECT count(*) FROM (SELECT visit_id, reason FROM points_ledger "
        "WHERE visit_id IS NOT NULL GROUP BY visit_id, reason HAVING count(*) > 1) d",
        "points for the same visit and reason",
    )
    op.create_index('uq_points_ledger_visit_reason', 'points_ledger', ['visit_id', 'reason'], unique=True, postgresql_where=sa.text('visit_id IS NOT NULL'))
    _check('points_ledger', 'note_on_adjustments', "(reason = 'adjustment') = (note IS NOT NULL)")
    _check('points_ledger', 'admin_only_on_adjustments', "reason = 'adjustment' OR admin_user_id IS NULL")


# --- bill-photo claims ------------------------------------------------------------------------


def _claims() -> None:
    op.add_column('visit_claims', sa.Column('reject_code', sa.String(length=16), nullable=True))
    op.add_column('visit_claims', sa.Column('expired_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('visit_claims', sa.Column('bill_number', sa.String(length=40), nullable=True))
    op.add_column('visit_claims', sa.Column('bill_amount_paise', sa.BigInteger(), nullable=True))
    op.add_column('visit_claims', sa.Column('bill_date', sa.Date(), nullable=True))
    op.add_column('visit_claims', sa.Column('photo_sha256', sa.String(length=64), nullable=True))
    op.add_column('visit_claims', sa.Column('customer_note', sa.String(length=300), nullable=True))
    op.add_column('visit_claims', sa.Column('admin_note', sa.Text(), nullable=True))
    op.create_index('ix_visit_claims_photo_sha256', 'visit_claims', ['photo_sha256'], unique=False)
    op.create_index('uq_visit_claims_venue_bill_number', 'visit_claims', ['venue_id', 'bill_number'], unique=True, postgresql_where=sa.text("bill_number IS NOT NULL AND status = 'approved'"))
    op.drop_index('uq_visit_claims_user_venue_day', table_name='visit_claims', postgresql_where=sa.text("status <> 'rejected'"))
    op.create_index('uq_visit_claims_user_venue_day', 'visit_claims', ['user_id', 'venue_id', 'visit_date'], unique=True, postgresql_where=sa.text("status NOT IN ('rejected', 'expired')"))
    # Earlier rejections only have free text: their code is "other", the text stays.
    op.execute("UPDATE visit_claims SET reject_code = 'other' WHERE status = 'rejected'")
    _drop_check('visit_claims', 'status_valid')
    _check('visit_claims', 'status_valid', _in('status', (
        'awaiting_photo', 'in_review', 'approved', 'rejected', 'expired',
    )))
    _drop_check('visit_claims', 'reason_when_rejected')
    _check('visit_claims', 'code_when_rejected', "(status = 'rejected') = (reject_code IS NOT NULL)")
    _check('visit_claims', 'reject_code_valid', _in('reject_code', (
        'unreadable', 'wrong_venue', 'wrong_date', 'duplicate_bill', 'other',
    )))
    _check('visit_claims', 'bill_amount_non_negative', 'bill_amount_paise >= 0')
    _check('visit_claims', 'photo_sha256_format', "photo_sha256 ~ '^[0-9a-f]{64}$'")
    _check('visit_claims', 'expired_at_when_expired', "(status = 'expired') = (expired_at IS NOT NULL)")


# --- referrals, Star Points -------------------------------------------------------------------


def _referrals_and_star_points() -> None:
    op.add_column('referrals', sa.Column('status', sa.String(length=10), server_default='pending', nullable=False))
    op.add_column('referrals', sa.Column('qualifying_visit_id', sa.Uuid(), nullable=True))
    op.add_column('referrals', sa.Column('source', sa.String(length=14), nullable=True))
    op.create_foreign_key(op.f('fk_referrals_qualifying_visit_id_visits'), 'referrals', 'visits', ['qualifying_visit_id'], ['id'], ondelete='SET NULL')
    # Rewarded if Star Points were given for it, capped if it was counted without them.
    op.execute(
        "UPDATE referrals SET status = CASE "
        "WHEN rewarded_at IS NULL THEN 'pending' "
        "WHEN EXISTS (SELECT 1 FROM star_points_ledger s WHERE s.referral_id = referrals.id) "
        "THEN 'rewarded' ELSE 'capped' END"
    )
    # The invited customer's first counted Notch up to the reward.
    op.execute(
        "UPDATE referrals SET qualifying_visit_id = ("
        "  SELECT v.id FROM visits v WHERE v.user_id = referrals.referred_id"
        "  AND v.status = 'valid' AND v.created_at <= referrals.rewarded_at"
        "  ORDER BY v.visited_at LIMIT 1"
        ") WHERE rewarded_at IS NOT NULL"
    )
    _check('referrals', 'status_valid', _in('status', ('pending', 'rewarded', 'capped', 'cancelled')))
    _check('referrals', 'source_valid', _in('source', ('link', 'typed_handle')))
    _check('referrals', 'rewarded_at_unless_pending', "(status = 'pending') = (rewarded_at IS NULL)")

    op.add_column('star_points_ledger', sa.Column('admin_user_id', sa.Uuid(), nullable=True))
    op.add_column('star_points_ledger', sa.Column('note', sa.String(length=255), nullable=True))
    op.create_foreign_key(op.f('fk_star_points_ledger_admin_user_id_admin_users'), 'star_points_ledger', 'admin_users', ['admin_user_id'], ['id'], ondelete='SET NULL')
    op.drop_constraint(op.f('uq_star_points_ledger_referral_id'), 'star_points_ledger', type_='unique')
    op.create_index('uq_star_points_ledger_referral_reason', 'star_points_ledger', ['referral_id', 'reason'], unique=True, postgresql_where=sa.text('referral_id IS NOT NULL'))
    op.execute(
        sa.text(
            "UPDATE star_points_ledger SET note = :note WHERE reason = 'adjustment'"
        ).bindparams(note=NOT_RECORDED)
    )
    _drop_check('star_points_ledger', 'reason_valid')
    _check('star_points_ledger', 'reason_valid', _in('reason', ('referral', 'referral_reversed', 'adjustment')))
    _check('star_points_ledger', 'referral_positive', "reason <> 'referral' OR points > 0")
    _check('star_points_ledger', 'reversal_negative', "reason <> 'referral_reversed' OR points < 0")
    _check('star_points_ledger', 'note_on_adjustments', "(reason = 'adjustment') = (note IS NOT NULL)")
    _check('star_points_ledger', 'admin_only_on_adjustments', "reason = 'adjustment' OR admin_user_id IS NULL")


# --- tags -------------------------------------------------------------------------------------


def _tags() -> None:
    op.add_column('tags', sa.Column('status', sa.String(length=10), server_default='active', nullable=False))
    op.add_column('tags', sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False))
    op.add_column('tags', sa.Column('emoji', sa.String(length=16), nullable=True))
    op.add_column('tags', sa.Column('aliases', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False))
    op.add_column('tags', sa.Column('categories', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False))
    op.add_column('tags', sa.Column('suggested_by_member_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f('fk_tags_suggested_by_member_id_venue_members'), 'tags', 'venue_members', ['suggested_by_member_id'], ['id'], ondelete='SET NULL')
    _check('tags', 'status_valid', _in('status', ('active', 'hidden', 'suggested')))
    _check('tags', 'categories_valid', f"categories <@ ARRAY[{', '.join(f"'{c}'" for c in CATEGORIES)}]::text[]")

    op.add_column('venue_tags', sa.Column('is_primary', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.create_index('uq_venue_tags_primary', 'venue_tags', ['venue_id'], unique=True, postgresql_where=sa.text('is_primary'))

    op.add_column('venue_items', sa.Column('price_paise', sa.BigInteger(), nullable=True))
    op.add_column('venue_items', sa.Column('is_signature', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('venue_items', sa.Column('photo_s3_key', sa.String(length=255), nullable=True))
    op.add_column('venue_items', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    _check('venue_items', 'price_paise_non_negative', 'price_paise >= 0')

    op.add_column('visit_tags', sa.Column('liked', sa.Boolean(), nullable=True))


# --- WhatsApp ---------------------------------------------------------------------------------


def _whatsapp() -> None:
    op.add_column('whatsapp_messages', sa.Column('in_reply_to_id', sa.Uuid(), nullable=True))
    op.add_column('whatsapp_messages', sa.Column('error_title', sa.String(length=255), nullable=True))
    op.add_column('whatsapp_messages', sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('whatsapp_messages', sa.Column('read_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(op.f('fk_whatsapp_messages_in_reply_to_id_whatsapp_messages'), 'whatsapp_messages', 'whatsapp_messages', ['in_reply_to_id'], ['id'], ondelete='SET NULL')
    _check('whatsapp_messages', 'no_templates', "message_type <> 'template' AND template_name IS NULL")


# --- downgrade --------------------------------------------------------------------------------
# Back to 0011. New states map to the nearest old one; data only 0012 can hold is dropped.


def downgrade() -> None:
    _drop_check('whatsapp_messages', 'no_templates')
    op.drop_constraint(op.f('fk_whatsapp_messages_in_reply_to_id_whatsapp_messages'), 'whatsapp_messages', type_='foreignkey')
    for column in ('read_at', 'delivered_at', 'error_title', 'in_reply_to_id'):
        op.drop_column('whatsapp_messages', column)

    op.drop_column('visit_tags', 'liked')
    for column in ('updated_at', 'photo_s3_key', 'is_signature', 'price_paise'):
        op.drop_column('venue_items', column)
    op.drop_index('uq_venue_tags_primary', table_name='venue_tags', postgresql_where=sa.text('is_primary'))
    op.drop_column('venue_tags', 'is_primary')
    op.drop_constraint(op.f('fk_tags_suggested_by_member_id_venue_members'), 'tags', type_='foreignkey')
    for column in ('suggested_by_member_id', 'categories', 'aliases', 'emoji', 'sort_order', 'status'):
        op.drop_column('tags', column)

    # Star Points: reversals become adjustments (the old schema's only negative kind).
    for name in ('admin_only_on_adjustments', 'note_on_adjustments', 'reversal_negative', 'referral_positive', 'reason_valid'):
        _drop_check('star_points_ledger', name)
    op.execute(
        "UPDATE star_points_ledger SET reason = 'adjustment', referral_id = NULL "
        "WHERE reason = 'referral_reversed'"
    )
    _check('star_points_ledger', 'reason_valid', _in('reason', ('referral', 'adjustment')))
    op.drop_index('uq_star_points_ledger_referral_reason', table_name='star_points_ledger', postgresql_where=sa.text('referral_id IS NOT NULL'))
    op.create_unique_constraint(op.f('uq_star_points_ledger_referral_id'), 'star_points_ledger', ['referral_id'])
    op.drop_constraint(op.f('fk_star_points_ledger_admin_user_id_admin_users'), 'star_points_ledger', type_='foreignkey')
    op.drop_column('star_points_ledger', 'note')
    op.drop_column('star_points_ledger', 'admin_user_id')
    op.drop_constraint(op.f('fk_referrals_qualifying_visit_id_visits'), 'referrals', type_='foreignkey')
    for column in ('source', 'qualifying_visit_id', 'status'):
        op.drop_column('referrals', column)

    # Claims: expired ones become rejected with a reason, as the old schema needs.
    for name in ('expired_at_when_expired', 'photo_sha256_format', 'bill_amount_non_negative', 'reject_code_valid', 'code_when_rejected', 'status_valid'):
        _drop_check('visit_claims', name)
    op.execute(
        "UPDATE visit_claims SET status = 'rejected', reject_reason = 'Expired' "
        "WHERE status = 'expired'"
    )
    op.execute(
        "UPDATE visit_claims SET reject_reason = 'Rejected' "
        "WHERE status = 'rejected' AND reject_reason IS NULL"
    )
    _check('visit_claims', 'status_valid', _in('status', ('awaiting_photo', 'in_review', 'approved', 'rejected')))
    _check('visit_claims', 'reason_when_rejected', "(status = 'rejected') = (reject_reason IS NOT NULL)")
    op.drop_index('uq_visit_claims_user_venue_day', table_name='visit_claims', postgresql_where=sa.text("status NOT IN ('rejected', 'expired')"))
    op.create_index('uq_visit_claims_user_venue_day', 'visit_claims', ['user_id', 'venue_id', 'visit_date'], unique=True, postgresql_where=sa.text("status <> 'rejected'"))
    op.drop_index('uq_visit_claims_venue_bill_number', table_name='visit_claims', postgresql_where=sa.text("bill_number IS NOT NULL AND status = 'approved'"))
    op.drop_index('ix_visit_claims_photo_sha256', table_name='visit_claims')
    for column in ('admin_note', 'customer_note', 'photo_sha256', 'bill_date', 'bill_amount_paise', 'bill_number', 'expired_at', 'reject_code'):
        op.drop_column('visit_claims', column)

    for name in ('admin_only_on_adjustments', 'note_on_adjustments'):
        _drop_check('points_ledger', name)
    op.drop_index('uq_points_ledger_visit_reason', table_name='points_ledger', postgresql_where=sa.text('visit_id IS NOT NULL'))
    op.drop_constraint(op.f('fk_points_ledger_city_id_cities'), 'points_ledger', type_='foreignkey')
    op.drop_constraint(op.f('fk_points_ledger_admin_user_id_admin_users'), 'points_ledger', type_='foreignkey')
    op.drop_index(op.f('ix_points_ledger_city_id'), table_name='points_ledger')
    for column in ('city_id', 'note', 'admin_user_id'):
        op.drop_column('points_ledger', column)

    op.drop_index('ix_visit_feedback_venue_created_at', table_name='visit_feedback')
    op.drop_index(op.f('ix_visit_feedback_user_id'), table_name='visit_feedback')
    op.drop_table('visit_feedback')
    op.drop_constraint(op.f('fk_visits_revoked_by_admin_id_admin_users'), 'visits', type_='foreignkey')
    for column in ('revoked_by_admin_id', 'revoked_reason', 'revoked_at', 'niche'):
        op.drop_column('visits', column)

    for name in ('code_age_non_negative', 'source_valid', 'reject_reason_valid'):
        _drop_check('scan_sessions', name)
    op.execute("UPDATE scan_sessions SET reject_reason = 'wrong_code' WHERE reject_reason = 'too_many_attempts'")
    _check('scan_sessions', 'reject_reason_valid', _in('reject_reason', (
        'unknown_venue', 'venue_unavailable', 'wrong_code', 'expired_code', 'code_used',
        'already_today', 'daily_limit', 'blocked_customer',
    )))
    op.drop_column('scan_sessions', 'code_age_s')
    op.drop_column('scan_sessions', 'source')

    # Applications: still-open needs_info -> pending, withdrawn -> rejected.
    for name in ('instagram_handle_format', 'pincode_format', 'heard_from_valid', 'status_valid'):
        _drop_check('venue_applications', name)
    op.execute("UPDATE venue_applications SET status = 'pending' WHERE status = 'needs_info'")
    op.execute(
        "UPDATE venue_applications SET status = 'rejected', rejection_reason = 'Withdrawn' "
        "WHERE status = 'withdrawn'"
    )
    _check('venue_applications', 'status_valid', _in('status', ('pending', 'approved', 'rejected')))
    op.drop_index('uq_venue_applications_open_email', table_name='venue_applications', postgresql_where=sa.text("status IN ('pending', 'needs_info')"))
    op.create_index(op.f('uq_venue_applications_pending_email'), 'venue_applications', ['email'], unique=True, postgresql_where=sa.text("status = 'pending'"))
    op.drop_constraint(op.f('fk_venue_applications_area_id_areas'), 'venue_applications', type_='foreignkey')
    for column in ('needs_info_message', 'admin_notes', 'heard_from', 'instagram_handle', 'pincode', 'area_suggestion', 'area_id'):
        op.drop_column('venue_applications', column)

    for name in ('active_is_paired', 'revoke_reason_when_revoked', 'revoked_at_when_revoked', 'revoke_reason_valid', 'status_valid'):
        _drop_check('counter_devices', name)
    op.add_column('counter_devices', sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.execute("UPDATE counter_devices SET is_active = (status <> 'revoked')")
    op.drop_index('uq_counter_devices_active_venue', table_name='counter_devices', postgresql_where=sa.text("status <> 'revoked'"))
    op.create_index(op.f('uq_counter_devices_active_venue'), 'counter_devices', ['venue_id'], unique=True, postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_constraint(op.f('fk_counter_devices_revoked_by_member_id_venue_members'), 'counter_devices', type_='foreignkey')
    for column in ('push_token', 'device_model', 'app_version', 'revoked_by_member_id', 'revoke_reason', 'status'):
        op.drop_column('counter_devices', column)

    for name in ('mfa_has_secret', 'active_has_password', 'disabled_at_only_when_disabled', 'status_valid'):
        _drop_check('venue_members', name)
    op.add_column('venue_members', sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.execute("UPDATE venue_members SET is_active = (status <> 'disabled')")
    op.drop_constraint(op.f('fk_venue_members_invited_by_member_id_venue_members'), 'venue_members', type_='foreignkey')
    for column in ('mfa_enabled_at', 'mfa_secret_enc', 'invited_by_member_id', 'disabled_at', 'password_changed_at', 'status'):
        op.drop_column('venue_members', column)

    op.drop_index('ix_venue_photos_venue_id_sort_order', table_name='venue_photos')
    op.drop_table('venue_photos')
    for name in ('price_range_valid', 'pincode_format'):
        _drop_check('venues', name)
    op.drop_index('uq_venues_google_place_id', table_name='venues', postgresql_where=sa.text('google_place_id IS NOT NULL'))
    for column in ('logo_s3_key', 'price_range', 'google_place_id', 'pincode', 'status_reason', 'closed_at', 'live_at', 'signed_at'):
        op.drop_column('venues', column)

    op.drop_index('ix_consents_venue_application_id', table_name='consents')
    op.drop_index('ix_consents_user_purpose_accepted_at', table_name='consents')
    op.drop_table('consents')
    for name in ('blocked_at_only_when_blocked', 'blocked_reason_when_blocked'):
        _drop_check('users', name)
    op.drop_column('users', 'blocked_reason')
    op.drop_column('users', 'blocked_at')

    for name in ('revoke_reason_when_revoked', 'revoke_reason_valid'):
        _drop_check('auth_tokens', name)
    op.drop_column('auth_tokens', 'revoke_reason')

    # Dropping the parent drops every partition with it.
    op.drop_index('ix_audit_logs_entity', table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_created_at'), table_name='audit_logs')
    op.drop_index('ix_audit_logs_actor', table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_table('audit_logs')

    for name in ('mfa_has_secret', 'active_has_password', 'disabled_at_only_when_disabled', 'status_valid'):
        _drop_check('admin_users', name)
    op.add_column('admin_users', sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.execute("UPDATE admin_users SET is_active = (status <> 'disabled')")
    op.add_column('admin_users', sa.Column('role', sa.String(length=12), server_default='reviewer', nullable=False))
    op.execute(
        "UPDATE admin_users SET role = 'admin' "
        "FROM admin_roles WHERE admin_roles.id = admin_users.role_id AND admin_roles.is_owner"
    )
    _check('admin_users', 'role_valid', "role IN ('admin', 'reviewer')")
    op.drop_constraint(op.f('fk_admin_users_disabled_by_admin_id_admin_users'), 'admin_users', type_='foreignkey')
    op.drop_constraint(op.f('fk_admin_users_invited_by_admin_id_admin_users'), 'admin_users', type_='foreignkey')
    op.drop_constraint(op.f('fk_admin_users_role_id_admin_roles'), 'admin_users', type_='foreignkey')
    op.drop_index(op.f('ix_admin_users_role_id'), table_name='admin_users')
    for column in ('phone_e164', 'disabled_by_admin_id', 'disabled_at', 'invited_by_admin_id', 'mfa_enabled_at', 'mfa_secret_enc', 'password_changed_at', 'status', 'role_id'):
        op.drop_column('admin_users', column)
    op.drop_table('admin_role_permissions')
    op.drop_index('uq_admin_roles_owner', table_name='admin_roles', postgresql_where=sa.text('is_owner'))
    op.drop_index('uq_admin_roles_name_lower', table_name='admin_roles')
    op.drop_table('admin_roles')

    for name in ('coordinates_paired', 'longitude_range', 'latitude_range', 'pincodes_format', 'source_valid', 'status_valid'):
        _drop_check('areas', name)
    op.drop_constraint('uq_areas_source_external_id', 'areas', type_='unique')
    for column in ('sort_order', 'pincodes', 'aliases', 'external_id', 'source', 'longitude', 'latitude', 'status'):
        op.drop_column('areas', column)

    for name in ('coordinates_paired', 'longitude_range', 'latitude_range', 'country_format', 'status_valid'):
        _drop_check('cities', name)
    op.add_column('cities', sa.Column('is_active', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.execute("UPDATE cities SET is_active = (status = 'live')")
    op.drop_index(op.f('ix_cities_status'), table_name='cities')
    for column in ('image_s3_key', 'country', 'sort_order', 'launched_at', 'longitude', 'latitude', 'status'):
        op.drop_column('cities', column)
