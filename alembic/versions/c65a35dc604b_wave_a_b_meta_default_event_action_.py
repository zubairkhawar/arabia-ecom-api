"""wave A+B: meta default_event/action_source, click utm_content/term/landing_url/wa_number/pool_number_id/capi_lead_sent, meta is_pixel/capi_verified

Revision ID: c65a35dc604b
Revises: 350eb06f22ae
Create Date: 2026-06-20 15:57:02.614085

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c65a35dc604b'
down_revision: Union[str, None] = '350eb06f22ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # click_sessions: new fields (nullable except booleans which get a default)
    op.add_column('click_sessions', sa.Column('utm_content', sa.String(length=120), nullable=True))
    op.add_column('click_sessions', sa.Column('utm_term', sa.String(length=120), nullable=True))
    op.add_column('click_sessions', sa.Column('landing_url', sa.Text(), nullable=True))
    op.add_column('click_sessions', sa.Column('wa_number', sa.String(length=32), nullable=True))
    op.add_column('click_sessions', sa.Column('pool_number_id', sa.String(length=32), nullable=True))
    op.add_column(
        'click_sessions',
        sa.Column('capi_lead_sent', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_foreign_key(
        'fk_click_sessions_pool_number_id',
        'click_sessions', 'pool_numbers',
        ['pool_number_id'], ['id'],
        ondelete='SET NULL',
    )

    # meta_configs: new fields with sensible defaults so existing rows backfill
    op.add_column(
        'meta_configs',
        sa.Column('default_event', sa.String(length=32), nullable=False, server_default='InitiateCheckout'),
    )
    op.add_column(
        'meta_configs',
        sa.Column('action_source', sa.String(length=32), nullable=False, server_default='website'),
    )
    op.add_column(
        'meta_configs',
        sa.Column('is_pixel_verified', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        'meta_configs',
        sa.Column('is_capi_verified', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Promote existing rows: if `verified` was true, treat it as is_capi_verified
    op.execute("UPDATE meta_configs SET is_capi_verified = verified WHERE verified = true")


def downgrade() -> None:
    op.drop_column('meta_configs', 'is_capi_verified')
    op.drop_column('meta_configs', 'is_pixel_verified')
    op.drop_column('meta_configs', 'action_source')
    op.drop_column('meta_configs', 'default_event')
    op.drop_constraint('fk_click_sessions_pool_number_id', 'click_sessions', type_='foreignkey')
    op.drop_column('click_sessions', 'capi_lead_sent')
    op.drop_column('click_sessions', 'pool_number_id')
    op.drop_column('click_sessions', 'wa_number')
    op.drop_column('click_sessions', 'landing_url')
    op.drop_column('click_sessions', 'utm_term')
    op.drop_column('click_sessions', 'utm_content')
