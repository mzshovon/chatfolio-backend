"""drop llm_models.is_fallback: the default/fallback model is configured via the environment

Revision ID: b0d4f8a2c6e9
Revises: a9c3e7b1d5f8
Create Date: 2026-10-17 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b0d4f8a2c6e9'
down_revision: str | None = 'a9c3e7b1d5f8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index('uq_llm_models_single_fallback', table_name='llm_models')
    op.drop_column('llm_models', 'is_fallback')


def downgrade() -> None:
    op.add_column(
        'llm_models',
        sa.Column('is_fallback', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        'uq_llm_models_single_fallback',
        'llm_models',
        ['is_fallback'],
        unique=True,
        postgresql_where=sa.text('is_fallback'),
    )
