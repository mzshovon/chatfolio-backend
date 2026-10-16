"""create user_model_preferences

Revision ID: e6f0b4c8d2a5
Revises: d5e9a3b7c1f4
Create Date: 2026-10-16 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e6f0b4c8d2a5'
down_revision: str | None = 'd5e9a3b7c1f4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'user_model_preferences',
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            primary_key=True,
        ),
        sa.Column(
            'model_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('llm_models.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table('user_model_preferences')
