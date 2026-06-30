from typing import Optional
from datetime import datetime
from sqlalchemy import String, ForeignKey, Text, Integer, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import IdMixin, TimestampMixin


class ShopifyStore(Base, IdMixin, TimestampMixin):
    """A reseller can connect multiple Shopify stores. Each store gives us
    a long-lived Admin API access token (shpat_...) that we use to sync
    products + (later) listen to order webhooks.

    access_token_enc is encrypted at rest with Fernet."""

    __tablename__ = "shopify_stores"

    reseller_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resellers.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    shop_domain: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    api_version: Mapped[str] = mapped_column(String(16), default="2024-10")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_orders_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    products_synced: Mapped[int] = mapped_column(Integer, default=0)
    orders_synced: Mapped[int] = mapped_column(Integer, default=0)
