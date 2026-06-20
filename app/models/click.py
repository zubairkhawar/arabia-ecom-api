from typing import Optional
from sqlalchemy import String, ForeignKey, JSON, Text, DateTime, Boolean, Float
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

from ..db import Base
from ._base import IdMixin, TimestampMixin


class ClickSession(Base, IdMixin, TimestampMixin):
    """Records every click on a product link.
    The `ref_token` is embedded in the WhatsApp prefilled message ([ref:c_xxxxxxxx])
    so the inbound webhook can match the chat back to this click."""

    __tablename__ = "click_sessions"

    reseller_id: Mapped[str] = mapped_column(String(32), ForeignKey("resellers.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(String(32), ForeignKey("products.id", ondelete="CASCADE"))
    ref_token: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)

    src_platform: Mapped[str] = mapped_column(String(16), default="other")  # tiktok|meta|snapchat|google|other
    # platform click IDs
    fbclid: Mapped[Optional[str]] = mapped_column(String(255))
    fbp: Mapped[Optional[str]] = mapped_column(String(255))
    fbc: Mapped[Optional[str]] = mapped_column(String(255))
    ttclid: Mapped[Optional[str]] = mapped_column(String(255))
    sclid: Mapped[Optional[str]] = mapped_column(String(255))
    gclid: Mapped[Optional[str]] = mapped_column(String(255))

    utm_source: Mapped[Optional[str]] = mapped_column(String(64))
    utm_medium: Mapped[Optional[str]] = mapped_column(String(64))
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(120))
    utm_content: Mapped[Optional[str]] = mapped_column(String(120))
    utm_term: Mapped[Optional[str]] = mapped_column(String(120))

    ip: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    referer: Mapped[Optional[str]] = mapped_column(Text)
    landing_url: Mapped[Optional[str]] = mapped_column(Text)

    # resolved routing
    wa_number: Mapped[Optional[str]] = mapped_column(String(32))
    pool_number_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("pool_numbers.id", ondelete="SET NULL"))

    # event state
    matched_chat_id: Mapped[Optional[str]] = mapped_column(String(32))
    add_to_cart_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    add_to_cart_event_id: Mapped[Optional[str]] = mapped_column(String(64))
    capi_lead_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    purchase_event_id: Mapped[Optional[str]] = mapped_column(String(64))


class AttributionEvent(Base, IdMixin, TimestampMixin):
    """Log of every server-side event we attempted to dispatch (CAPI/TikTok Events/etc).
    Useful for debugging the famous 'events not showing up' problem."""

    __tablename__ = "attribution_events"

    reseller_id: Mapped[str] = mapped_column(String(32), ForeignKey("resellers.id", ondelete="CASCADE"), index=True)
    click_session_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("click_sessions.id", ondelete="SET NULL"))
    order_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("orders.id", ondelete="SET NULL"))

    platform: Mapped[str] = mapped_column(String(16))    # meta | tiktok | snapchat | google
    event_name: Mapped[str] = mapped_column(String(64))  # AddToCart | Purchase | Lead | InitiateCheckout
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[Optional[float]] = mapped_column(Float)
    currency: Mapped[Optional[str]] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|sent|skipped|failed
    response_code: Mapped[Optional[int]] = mapped_column()
    response_body: Mapped[Optional[str]] = mapped_column(Text)
    payload: Mapped[Optional[dict]] = mapped_column(JSON)
