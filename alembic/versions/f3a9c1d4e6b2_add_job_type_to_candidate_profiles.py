"""add job_type to candidate_profiles

Revision ID: f3a9c1d4e6b2
Revises: 3ca071fdd13b
Create Date: 2026-09-07 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'f3a9c1d4e6b2'
down_revision: str | None = '3ca071fdd13b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Uppercase to match SQLAlchemy's default enum-binding behavior (it sends the Python enum
    # member's `.name`, not `.value`) — same convention as the `profile_status` enum.
    job_type_enum = sa.Enum('REMOTE', 'ONSITE', 'HYBRID', name='job_type')
    job_type_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        'candidate_profiles',
        sa.Column('job_type', job_type_enum, nullable=True),
    )


def downgrade() -> None:
    op.drop_column('candidate_profiles', 'job_type')
    sa.Enum(name='job_type').drop(op.get_bind(), checkfirst=True)
