"""create feedback table

Revision ID: 7c1d5e9a3f2b
Revises: f3a9c1d4e6b2
Create Date: 2026-09-11 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '7c1d5e9a3f2b'
down_revision: str | None = 'f3a9c1d4e6b2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'feedback',
        sa.Column('nps_score', sa.Integer(), nullable=False),
        sa.Column('message', sa.String(length=2100), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.CheckConstraint('nps_score >= 0 AND nps_score <= 5', name='ck_feedback_nps_score_range'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('feedback')
