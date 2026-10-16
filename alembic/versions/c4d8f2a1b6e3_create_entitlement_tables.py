"""create features, plans, plan_features, subscriptions, entitlement_overrides (+ seed)

Revision ID: c4d8f2a1b6e3
Revises: a8c3e5f7b9d1
Create Date: 2026-10-16 00:00:00.000000

"""
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'c4d8f2a1b6e3'
down_revision: str | None = 'a8c3e5f7b9d1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        'features',
        sa.Column('key', sa.String(length=64), primary_key=True),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('value_type', sa.String(length=32), nullable=False),
        sa.Column('default_period', sa.String(length=32), nullable=True),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
    )

    op.create_table(
        'plans',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('slug', sa.String(length=63), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('tagline', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('is_public', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('is_default_free', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('price_minor', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='BDT'),
        sa.Column('duration_unit', sa.String(length=32), nullable=False, server_default='lifetime'),
        sa.Column('duration_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('external_product_ref', sa.String(length=128), nullable=True),
        sa.Column('trial_days', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('badge_text', sa.String(length=50), nullable=True),
        sa.Column('cta_label', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('footnote', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('marketing_bullets', sa.JSON(), nullable=False),
        *_timestamps(),
    )
    op.create_index('ix_plans_slug', 'plans', ['slug'], unique=True)
    op.create_index(
        'uq_plans_single_default_free',
        'plans',
        ['is_default_free'],
        unique=True,
        postgresql_where=sa.text('is_default_free'),
    )

    op.create_table(
        'plan_features',
        sa.Column(
            'plan_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('plans.id', ondelete='CASCADE'),
            primary_key=True,
        ),
        sa.Column(
            'feature_key',
            sa.String(length=64),
            sa.ForeignKey('features.key', ondelete='CASCADE'),
            primary_key=True,
        ),
        sa.Column('int_value', sa.Integer(), nullable=True),
        sa.Column('bool_value', sa.Boolean(), nullable=True),
        sa.Column('period', sa.String(length=32), nullable=True),
    )

    op.create_table(
        'subscriptions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'plan_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('plans.id', ondelete='RESTRICT'),
            nullable=False,
        ),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column(
            'starts_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index('ix_subscriptions_user_id', 'subscriptions', ['user_id'])
    op.create_index('ix_subscriptions_plan_id', 'subscriptions', ['plan_id'])
    op.create_index(
        'uq_subscriptions_one_active_per_user',
        'subscriptions',
        ['user_id'],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        'entitlement_overrides',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'feature_key',
            sa.String(length=64),
            sa.ForeignKey('features.key', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('int_value', sa.Integer(), nullable=True),
        sa.Column('bool_value', sa.Boolean(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reason', sa.String(length=500), nullable=False, server_default=''),
        sa.Column(
            'granted_by',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint('user_id', 'feature_key', name='uq_override_user_feature'),
    )
    op.create_index('ix_entitlement_overrides_user_id', 'entitlement_overrides', ['user_id'])

    _seed()


def _seed() -> None:
    """Default catalogue + the free and premium plans, taken from `entitlements.catalog` so there
    is one definition of the defaults (the test suite seeds from the same place)."""
    from chatfolio.entitlements.catalog import DEFAULT_PLANS, FEATURES

    features = sa.table(
        'features',
        sa.column('key', sa.String),
        sa.column('kind', sa.String),
        sa.column('value_type', sa.String),
        sa.column('default_period', sa.String),
        sa.column('description', sa.Text),
    )
    op.bulk_insert(
        features,
        [
            {
                'key': f.key.value,
                'kind': f.kind.value,
                'value_type': f.value_type.value,
                'default_period': f.default_period.value if f.default_period else None,
                'description': f.description,
            }
            for f in FEATURES
        ],
    )

    plans = sa.table(
        'plans',
        sa.column('id', postgresql.UUID(as_uuid=True)),
        sa.column('slug', sa.String),
        sa.column('name', sa.String),
        sa.column('tagline', sa.String),
        sa.column('sort_order', sa.Integer),
        sa.column('price_minor', sa.Integer),
        sa.column('currency', sa.String),
        sa.column('duration_unit', sa.String),
        sa.column('duration_count', sa.Integer),
        sa.column('cta_label', sa.String),
        sa.column('footnote', sa.String),
        sa.column('badge_text', sa.String),
        sa.column('marketing_bullets', sa.JSON),
        sa.column('is_default_free', sa.Boolean),
    )
    plan_features = sa.table(
        'plan_features',
        sa.column('plan_id', postgresql.UUID(as_uuid=True)),
        sa.column('feature_key', sa.String),
        sa.column('int_value', sa.Integer),
        sa.column('bool_value', sa.Boolean),
        sa.column('period', sa.String),
    )

    plan_rows = []
    feature_rows = []
    for seed in DEFAULT_PLANS:
        plan_id = uuid.uuid4()
        plan_rows.append(
            {
                'id': plan_id,
                'slug': seed.slug,
                'name': seed.name,
                'tagline': seed.tagline,
                'sort_order': seed.sort_order,
                'price_minor': seed.price_minor,
                'currency': seed.currency,
                'duration_unit': seed.duration_unit.value,
                'duration_count': seed.duration_count,
                'cta_label': seed.cta_label,
                'footnote': seed.footnote,
                'badge_text': seed.badge_text,
                'marketing_bullets': list(seed.bullets),
                'is_default_free': seed.is_default_free,
            }
        )
        for key, value in seed.features.items():
            feature_rows.append(
                {
                    'plan_id': plan_id,
                    'feature_key': key.value,
                    'int_value': value.int_value,
                    'bool_value': value.bool_value,
                    'period': value.period.value if value.period else None,
                }
            )
    op.bulk_insert(plans, plan_rows)
    op.bulk_insert(plan_features, feature_rows)


def downgrade() -> None:
    op.drop_index('ix_entitlement_overrides_user_id', table_name='entitlement_overrides')
    op.drop_table('entitlement_overrides')
    op.drop_index('uq_subscriptions_one_active_per_user', table_name='subscriptions')
    op.drop_index('ix_subscriptions_plan_id', table_name='subscriptions')
    op.drop_index('ix_subscriptions_user_id', table_name='subscriptions')
    op.drop_table('subscriptions')
    op.drop_table('plan_features')
    op.drop_index('uq_plans_single_default_free', table_name='plans')
    op.drop_index('ix_plans_slug', table_name='plans')
    op.drop_table('plans')
    op.drop_table('features')
