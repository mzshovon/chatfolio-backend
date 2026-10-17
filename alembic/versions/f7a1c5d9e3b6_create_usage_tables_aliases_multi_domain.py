"""usage_counters, llm_usage, chatfolio_links; allow several domains per chatfolio

Revision ID: f7a1c5d9e3b6
Revises: e6f0b4c8d2a5
Create Date: 2026-10-16 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f7a1c5d9e3b6'
down_revision: str | None = 'e6f0b4c8d2a5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'usage_counters',
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            primary_key=True,
        ),
        sa.Column('feature_key', sa.String(length=64), primary_key=True),
        sa.Column('period_key', sa.String(length=16), primary_key=True),
        sa.Column('used', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_table(
        'llm_usage',
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            primary_key=True,
        ),
        sa.Column('period_key', sa.String(length=16), primary_key=True),
        sa.Column('model_label', sa.String(length=160), primary_key=True),
        sa.Column('tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('calls', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_table(
        'chatfolio_links',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'chatfolio_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('public_chatfolios.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('slug', sa.String(length=63), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index('ix_chatfolio_links_chatfolio_id', 'chatfolio_links', ['chatfolio_id'])
    op.create_index('ix_chatfolio_links_slug', 'chatfolio_links', ['slug'], unique=True)

    # A chatfolio may now have several domains (limited by the plan, not by the schema).
    op.drop_index('ix_public_domains_chatfolio_id', table_name='public_domains')
    op.create_index('ix_public_domains_chatfolio_id', 'public_domains', ['chatfolio_id'])


def downgrade() -> None:
    op.drop_index('ix_public_domains_chatfolio_id', table_name='public_domains')
    op.create_index(
        'ix_public_domains_chatfolio_id', 'public_domains', ['chatfolio_id'], unique=True
    )
    op.drop_index('ix_chatfolio_links_slug', table_name='chatfolio_links')
    op.drop_index('ix_chatfolio_links_chatfolio_id', table_name='chatfolio_links')
    op.drop_table('chatfolio_links')
    op.drop_table('llm_usage')
    op.drop_table('usage_counters')
