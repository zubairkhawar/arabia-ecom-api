from typing import Optional
from sqlalchemy import String, ForeignKey, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import IdMixin, TimestampMixin


class MetaConfig(Base, IdMixin, TimestampMixin):
    """Per-reseller Meta Pixel + Conversions API credentials.
    capi_access_token_enc is encrypted at rest with Fernet."""

    __tablename__ = "meta_configs"

    reseller_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resellers.id", ondelete="CASCADE"), unique=True
    )
    pixel_id: Mapped[Optional[str]] = mapped_column(String(64))
    capi_access_token_enc: Mapped[Optional[str]] = mapped_column(Text)
    test_event_code: Mapped[Optional[str]] = mapped_column(String(64))

    # Top-of-funnel event name to fire on link click (browser pixel + CAPI mirror).
    # Spec recommends 'InitiateCheckout' for click-to-WhatsApp intent.
    default_event: Mapped[str] = mapped_column(String(32), default="InitiateCheckout")

    # CAPI action_source — 'website' (default, works with strong fbc) or
    # 'business_messaging' (better attribution on CTWA-eligible accounts).
    action_source: Mapped[str] = mapped_column(String(32), default="website")

    is_pixel_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_capi_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # legacy aggregate flag (kept for back-compat with earlier code)
    verified: Mapped[bool] = mapped_column(default=False)
