"""create meeting_requests table

Revision ID: a8c3e5f7b9d1
Revises: 84feea352f12
Create Date: 2026-10-02 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a8c3e5f7b9d1'
down_revision: str | None = '84feea352f12'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'meeting_requests',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'chat_session_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('chat_sessions.id', ondelete='CASCADE'),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index('ix_meeting_requests_user_id', 'meeting_requests', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_meeting_requests_user_id', table_name='meeting_requests')
    op.drop_table('meeting_requests')
