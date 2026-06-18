from typing import Optional, List
from sqlalchemy import String, Integer, Float, ForeignKey, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime

from ..db import Base
from ._base import IdMixin, TimestampMixin


class Order(Base, IdMixin, TimestampMixin):
    __tablename__ = "orders"

    reseller_id: Mapped[str] = mapped_column(String(32), ForeignKey("resellers.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[str] = mapped_column(String(32), ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("chats.id", ondelete="SET NULL"))
    click_session_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("click_sessions.id", ondelete="SET NULL"))

    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)  # ORD-2041
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="AED")
    channel: Mapped[str] = mapped_column(String(16), default="whatsapp")  # whatsapp | shopify
    status: Mapped[str] = mapped_column(String(16), default="processing")  # processing | confirmed | hold | cancelled
    delivery_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|dispatched|in_transit|delivered|returned|failed
    tracking_number: Mapped[Optional[str]] = mapped_column(String(80))
    source: Mapped[Optional[str]] = mapped_column(String(120))  # human-readable source ("WA Link · earbuds-pro")
    source_platform: Mapped[Optional[str]] = mapped_column(String(16))  # tiktok|meta|snapchat|google|other

    follow_up_template_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("templates.id", ondelete="SET NULL"))
    follow_up_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    customer_address: Mapped[Optional[str]] = mapped_column(JSON)
    purchase_event_sent: Mapped[bool] = mapped_column(default=False)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    items: Mapped[List["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base, IdMixin):
    __tablename__ = "order_items"

    order_id: Mapped[str] = mapped_column(String(32), ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(String(32), ForeignKey("products.id", ondelete="RESTRICT"))
    variant_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("product_variants.id", ondelete="SET NULL"))
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)  # what we charged per unit at confirmation
    line_total: Mapped[float] = mapped_column(Float, nullable=False)

    order: Mapped["Order"] = relationship(back_populates="items")
