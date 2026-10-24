"""scan sessions visits and points

Scans module step 3, the spec sheet's tables:
- scan_sessions: every scan (one per inbound WhatsApp message), verified or rejected with a
  reason; which venue, counter phone and code. No phone numbers. Changes from the sheet: venue_id
  may be empty (a code matching no venue is still logged), no 'received' status (a scan is
  checked in one go), scan_code added, reject reasons widened.
- visits: one verified Notch, linked to its scan session; one per customer per venue per IST day
  (unique constraint, the final guard against two messages at once).
- points_ledger: every point with its reason (visit +10, new_venue +2, every_third_visit +2,
  adjustment). Replaces the sheet's new_place +10 / repeat +5.

users.handle becomes required (riya.m4821, made from the WhatsApp name). By hand: existing
customers get a handle before the NOT NULL, and the handle format CHECK (autogenerate does not
detect CHECK constraints).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-24 11:04:27.176926

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.modules.users.handles import HANDLE_REGEX, SHORT_DIGITS, handle_base, random_handle

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('scan_sessions',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('venue_id', sa.Uuid(), nullable=True),
    sa.Column('counter_device_id', sa.Uuid(), nullable=True),
    sa.Column('inbound_message_id', sa.Uuid(), nullable=False),
    sa.Column('scan_code', sa.String(length=10), nullable=True),
    sa.Column('code_submitted', sa.String(length=16), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('reject_reason', sa.String(length=20), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'rejected') = (reject_reason IS NOT NULL)", name=op.f('ck_scan_sessions_reject_reason_when_rejected')),
    sa.CheckConstraint("reject_reason IN ('unknown_venue', 'venue_unavailable', 'wrong_code', 'expired_code', 'code_used', 'already_today', 'daily_limit', 'blocked_customer')", name=op.f('ck_scan_sessions_reject_reason_valid')),
    sa.CheckConstraint("status IN ('verified', 'rejected')", name=op.f('ck_scan_sessions_status_valid')),
    sa.ForeignKeyConstraint(['counter_device_id'], ['counter_devices.id'], name=op.f('fk_scan_sessions_counter_device_id_counter_devices'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['inbound_message_id'], ['whatsapp_messages.id'], name=op.f('fk_scan_sessions_inbound_message_id_whatsapp_messages'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_scan_sessions_user_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['venue_id'], ['venues.id'], name=op.f('fk_scan_sessions_venue_id_venues'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scan_sessions')),
    sa.UniqueConstraint('inbound_message_id', name=op.f('uq_scan_sessions_inbound_message_id'))
    )
    op.create_index(op.f('ix_scan_sessions_status'), 'scan_sessions', ['status'], unique=False)
    op.create_index('ix_scan_sessions_user_created_at', 'scan_sessions', ['user_id', 'created_at'], unique=False)
    op.create_index('ix_scan_sessions_venue_created_at', 'scan_sessions', ['venue_id', 'created_at'], unique=False)
    op.create_table('visits',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('venue_id', sa.Uuid(), nullable=False),
    sa.Column('scan_session_id', sa.Uuid(), nullable=False),
    sa.Column('visit_date', sa.Date(), nullable=False),
    sa.Column('visited_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('notch_no', sa.Integer(), nullable=False),
    sa.Column('visit_no_at_venue', sa.Integer(), nullable=False),
    sa.Column('code_slot', sa.BigInteger(), nullable=False),
    sa.Column('verification_level', sa.String(length=10), server_default='basic', nullable=False),
    sa.Column('status', sa.String(length=10), server_default='valid', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('valid', 'revoked')", name=op.f('ck_visits_status_valid')),
    sa.CheckConstraint("verification_level IN ('basic', 'verified')", name=op.f('ck_visits_verification_level_valid')),
    sa.CheckConstraint('notch_no > 0', name=op.f('ck_visits_notch_no_positive')),
    sa.CheckConstraint('visit_no_at_venue > 0', name=op.f('ck_visits_visit_no_at_venue_positive')),
    sa.ForeignKeyConstraint(['scan_session_id'], ['scan_sessions.id'], name=op.f('fk_visits_scan_session_id_scan_sessions'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_visits_user_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['venue_id'], ['venues.id'], name=op.f('fk_visits_venue_id_venues'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_visits')),
    sa.UniqueConstraint('scan_session_id', name=op.f('uq_visits_scan_session_id')),
    sa.UniqueConstraint('user_id', 'venue_id', 'visit_date', name='uq_visits_user_venue_day'),
    sa.UniqueConstraint('venue_id', 'notch_no', name='uq_visits_venue_notch_no')
    )
    op.create_index('ix_visits_user_visited_at', 'visits', ['user_id', 'visited_at'], unique=False)
    op.create_index('ix_visits_venue_visited_at', 'visits', ['venue_id', 'visited_at'], unique=False)
    op.create_table('points_ledger',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('visit_id', sa.Uuid(), nullable=True),
    sa.Column('reason', sa.String(length=20), nullable=False),
    sa.Column('points', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("reason IN ('visit', 'new_venue', 'every_third_visit', 'adjustment')", name=op.f('ck_points_ledger_reason_valid')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_points_ledger_user_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['visit_id'], ['visits.id'], name=op.f('fk_points_ledger_visit_id_visits'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_points_ledger'))
    )
    op.create_index('ix_points_ledger_user_created_at', 'points_ledger', ['user_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_points_ledger_visit_id'), 'points_ledger', ['visit_id'], unique=False)

    # Customers created before handles existed get one now, from their name if they have one.
    connection = op.get_bind()
    used = {
        h for (h,) in connection.execute(sa.text("SELECT handle FROM users WHERE handle IS NOT NULL"))
    }
    missing = connection.execute(sa.text("SELECT id, name FROM users WHERE handle IS NULL"))
    for user_id, name in missing.all():
        handle = random_handle(handle_base(name), SHORT_DIGITS)
        while handle in used:
            handle = random_handle(handle_base(name), SHORT_DIGITS + 2)
        used.add(handle)
        connection.execute(
            sa.text("UPDATE users SET handle = :handle WHERE id = :id"),
            {"handle": handle, "id": user_id},
        )
    op.alter_column('users', 'handle',
               existing_type=sa.VARCHAR(length=30),
               nullable=False)
    op.create_check_constraint(
        op.f('ck_users_handle_format'), 'users', f"handle ~ '{HANDLE_REGEX}'"
    )


def downgrade() -> None:
    op.drop_constraint(op.f('ck_users_handle_format'), 'users', type_='check')
    op.alter_column('users', 'handle',
               existing_type=sa.VARCHAR(length=30),
               nullable=True)
    op.drop_index(op.f('ix_points_ledger_visit_id'), table_name='points_ledger')
    op.drop_index('ix_points_ledger_user_created_at', table_name='points_ledger')
    op.drop_table('points_ledger')
    op.drop_index('ix_visits_venue_visited_at', table_name='visits')
    op.drop_index('ix_visits_user_visited_at', table_name='visits')
    op.drop_table('visits')
    op.drop_index('ix_scan_sessions_venue_created_at', table_name='scan_sessions')
    op.drop_index('ix_scan_sessions_user_created_at', table_name='scan_sessions')
    op.drop_index(op.f('ix_scan_sessions_status'), table_name='scan_sessions')
    op.drop_table('scan_sessions')
