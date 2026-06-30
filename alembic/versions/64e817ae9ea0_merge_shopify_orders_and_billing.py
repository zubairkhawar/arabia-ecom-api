"""merge_shopify_orders_and_billing

Revision ID: 64e817ae9ea0
Revises: 7f3a9c2b5e81, ff95f04b1bf6
Create Date: 2026-07-01 00:26:26.934236

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '64e817ae9ea0'
down_revision: Union[str, None] = ('7f3a9c2b5e81', 'ff95f04b1bf6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
