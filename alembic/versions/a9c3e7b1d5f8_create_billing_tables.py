"""orders, licenses, app_settings; plan purchase_url/allowed_strategies; subscription order/reminder

Revision ID: a9c3e7b1d5f8
Revises: f7a1c5d9e3b6
Create Date: 2026-10-17 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a9c3e7b1d5f8'
down_revision: str | None = 'f7a1c5d9e3b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> list[sa.Column]:
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        'orders',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plan_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('plans.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('strategy', sa.String(length=40), nullable=False),
        sa.Column('external_ref', sa.String(length=128), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('amount_minor', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='BDT'),
        sa.Column('purchased_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('raw_response', sa.JSON(), nullable=True),
        *_ts(),
        sa.UniqueConstraint('strategy', 'external_ref', name='uq_orders_strategy_ref'),
    )
    op.create_index('ix_orders_user_id', 'orders', ['user_id'])

    op.create_table(
        'licenses',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('license_key_hash', sa.String(length=64), nullable=False),
        sa.Column('license_key_encrypted', sa.Text(), nullable=False),
        sa.Column('key_last4', sa.String(length=4), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plan_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('plans.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('order_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('orders.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('validated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_revalidated_at', sa.DateTime(timezone=True), nullable=True),
        *_ts(),
    )
    op.create_index('ix_licenses_license_key_hash', 'licenses', ['license_key_hash'], unique=True)
    op.create_index('ix_licenses_user_id', 'licenses', ['user_id'])

    op.create_table(
        'app_settings',
        sa.Column('key', sa.String(length=64), primary_key=True),
        sa.Column('value', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.add_column('plans', sa.Column('purchase_url', sa.String(length=500), nullable=True))
    op.add_column(
        'plans',
        sa.Column(
            'allowed_strategies',
            sa.JSON(),
            nullable=False,
            server_default=sa.text('\'["support_kori_license"]\''),
        ),
    )
    op.add_column(
        'subscriptions',
        sa.Column('order_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('orders.id', ondelete='SET NULL'), nullable=True),
    )
    op.add_column('subscriptions', sa.Column('reminder_sent_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_subscriptions_order_id', 'subscriptions', ['order_id'])
    op.create_index(
        'uq_subscriptions_one_trial_per_user',
        'subscriptions',
        ['user_id'],
        unique=True,
        postgresql_where=sa.text("source = 'trial'"),
    )


def downgrade() -> None:
    op.drop_index('uq_subscriptions_one_trial_per_user', table_name='subscriptions')
    op.drop_index('ix_subscriptions_order_id', table_name='subscriptions')
    op.drop_column('subscriptions', 'reminder_sent_at')
    op.drop_column('subscriptions', 'order_id')
    op.drop_column('plans', 'allowed_strategies')
    op.drop_column('plans', 'purchase_url')
    op.drop_table('app_settings')
    op.drop_index('ix_licenses_user_id', table_name='licenses')
    op.drop_index('ix_licenses_license_key_hash', table_name='licenses')
    op.drop_table('licenses')
    op.drop_index('ix_orders_user_id', table_name='orders')
    op.drop_table('orders')
