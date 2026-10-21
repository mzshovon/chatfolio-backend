"""add avatar_url, notice_period_days, work_hours to candidate_profiles

Revision ID: c1e5a9d3b7f2
Revises: b0d4f8a2c6e9
Create Date: 2026-10-21 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'c1e5a9d3b7f2'
down_revision: str | None = 'b0d4f8a2c6e9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('candidate_profiles', sa.Column('avatar_url', sa.String(1024), nullable=True))
    op.add_column('candidate_profiles', sa.Column('notice_period_days', sa.Integer(), nullable=True))
    op.add_column('candidate_profiles', sa.Column('work_hours', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('candidate_profiles', 'work_hours')
    op.drop_column('candidate_profiles', 'notice_period_days')
    op.drop_column('candidate_profiles', 'avatar_url')
