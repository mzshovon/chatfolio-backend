"""create llm_models + plan_models, add admin_audit_logs.details (+ seed fallback model)

Revision ID: d5e9a3b7c1f4
Revises: c4d8f2a1b6e3
Create Date: 2026-10-16 00:00:00.000000

"""
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd5e9a3b7c1f4'
down_revision: str | None = 'c4d8f2a1b6e3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'llm_models',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('model_id', sa.String(length=128), nullable=False),
        sa.Column('display_name', sa.String(length=100), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('is_fallback', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('supports_json_mode', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('cost_in_per_1m', sa.Numeric(10, 4), nullable=True),
        sa.Column('cost_out_per_1m', sa.Numeric(10, 4), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint('provider', 'model_id', name='uq_llm_models_provider_model'),
    )
    op.create_index(
        'uq_llm_models_single_fallback',
        'llm_models',
        ['is_fallback'],
        unique=True,
        postgresql_where=sa.text('is_fallback'),
    )

    op.create_table(
        'plan_models',
        sa.Column(
            'plan_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('plans.id', ondelete='CASCADE'),
            primary_key=True,
        ),
        sa.Column(
            'model_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('llm_models.id', ondelete='CASCADE'),
            primary_key=True,
        ),
    )

    op.add_column('admin_audit_logs', sa.Column('details', sa.JSON(), nullable=True))

    # Seed the DeepSeek fallback and let the two seeded plans use it.
    bind = op.get_bind()
    model_id = uuid.uuid4()
    bind.execute(
        sa.text(
            "INSERT INTO llm_models (id, provider, model_id, display_name, is_fallback) "
            "VALUES (:id, 'deepseek', 'deepseek-chat', 'DeepSeek', true)"
        ),
        {'id': model_id},
    )
    for (plan_id,) in bind.execute(sa.text('SELECT id FROM plans')).all():
        bind.execute(
            sa.text('INSERT INTO plan_models (plan_id, model_id) VALUES (:p, :m)'),
            {'p': plan_id, 'm': model_id},
        )


def downgrade() -> None:
    op.drop_column('admin_audit_logs', 'details')
    op.drop_table('plan_models')
    op.drop_index('uq_llm_models_single_fallback', table_name='llm_models')
    op.drop_table('llm_models')
