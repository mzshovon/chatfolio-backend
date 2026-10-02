"""create google_calendar_connections table

Revision ID: 84feea352f12
Revises: 7c1d5e9a3f2b
Create Date: 2026-09-14 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '84feea352f12'
down_revision: str | None = '7c1d5e9a3f2b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'google_calendar_connections',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
            unique=True,
        ),
        sa.Column('google_account_email', sa.String(length=255), nullable=True),
        sa.Column('access_token_encrypted', sa.Text(), nullable=False),
        sa.Column('refresh_token_encrypted', sa.Text(), nullable=True),
        sa.Column('access_token_expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('granted_scopes', sa.Text(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        'ix_google_calendar_connections_user_id',
        'google_calendar_connections',
        ['user_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_google_calendar_connections_user_id', table_name='google_calendar_connections')
    op.drop_table('google_calendar_connections')
