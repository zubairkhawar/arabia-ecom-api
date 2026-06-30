"""shopify orders sync: orders.shopify_store_id + shopify_order_id, shopify_stores.last_orders_sync_at

Revision ID: 7f3a9c2b5e81
Revises: 8a16de97e867
Create Date: 2026-06-30 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7f3a9c2b5e81'
down_revision: Union[str, None] = '8a16de97e867'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # orders: per-row Shopify dedup keys. Both nullable — non-Shopify
    # orders (WhatsApp flow) leave them NULL. Partial unique index below
    # enforces dedup only when both are present.
    op.add_column(
        'orders',
        sa.Column('shopify_store_id', sa.String(length=32), nullable=True),
    )
    op.add_column(
        'orders',
        sa.Column('shopify_order_id', sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        'fk_orders_shopify_store_id',
        'orders', 'shopify_stores',
        ['shopify_store_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'ix_orders_shopify_store_id',
        'orders', ['shopify_store_id'],
    )
    op.create_index(
        'ix_orders_shopify_order_id',
        'orders', ['shopify_order_id'],
    )
    # Partial unique index: enforces dedup only on Shopify-sourced orders,
    # leaves WhatsApp orders (both NULL) unconstrained.
    op.create_index(
        'uq_orders_shopify_dedup',
        'orders',
        ['shopify_store_id', 'shopify_order_id'],
        unique=True,
        postgresql_where=sa.text(
            'shopify_store_id IS NOT NULL AND shopify_order_id IS NOT NULL'
        ),
    )

    # shopify_stores: incremental-sync cursor. Only advances on a fully
    # successful order-sync pass; partial failures leave it unset so a
    # retry re-pulls the same window (dedup handles already-written rows).
    op.add_column(
        'shopify_stores',
        sa.Column('last_orders_sync_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('shopify_stores', 'last_orders_sync_at')
    op.drop_index('uq_orders_shopify_dedup', table_name='orders')
    op.drop_index('ix_orders_shopify_order_id', table_name='orders')
    op.drop_index('ix_orders_shopify_store_id', table_name='orders')
    op.drop_constraint('fk_orders_shopify_store_id', 'orders', type_='foreignkey')
    op.drop_column('orders', 'shopify_order_id')
    op.drop_column('orders', 'shopify_store_id')
