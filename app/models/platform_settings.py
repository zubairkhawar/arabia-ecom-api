from typing import Optional
from sqlalchemy import String, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import IdMixin, TimestampMixin


class PlatformSettings(Base, IdMixin, TimestampMixin):
    """Singleton — exactly one row, holds platform-wide defaults
    that the sole admin manages from /admin/settings.

    Used as defaults during reseller signup and as customer-facing
    branding / support contact across the portal.
    """

    __tablename__ = "platform_settings"

    # Branding
    platform_name: Mapped[str] = mapped_column(String(120), default="Arabia AI")
    support_email: Mapped[Optional[str]] = mapped_column(String(255))
    support_phone: Mapped[Optional[str]] = mapped_column(String(64))

    # AI defaults applied to new resellers' AISetting row at signup
    default_ai_name: Mapped[str] = mapped_column(String(64), default="Max")
    default_ai_tone: Mapped[str] = mapped_column(String(32), default="Friendly")
    default_response_length: Mapped[str] = mapped_column(String(16), default="Medium")
    default_opening_message: Mapped[Optional[str]] = mapped_column(
        Text, default="Hi! Welcome to {{brand}} 👋 How can I help?"
    )

    # Plan caps
    starter_chats_cap: Mapped[int] = mapped_column(Integer, default=500)
    growth_chats_cap: Mapped[int] = mapped_column(Integer, default=5000)
    scale_chats_cap: Mapped[Optional[int]] = mapped_column(Integer)  # null = unlimited
    pool_capacity_per_number: Mapped[int] = mapped_column(Integer, default=50)
    auto_escalate_after_msgs: Mapped[int] = mapped_column(Integer, default=3)
    ai_typing_delay_ms: Mapped[int] = mapped_column(Integer, default=900)

    # Setup guide video URLs
    wa_setup_video_url: Mapped[Optional[str]] = mapped_column(String(500))
    shopify_setup_video_url: Mapped[Optional[str]] = mapped_column(String(500))
    ai_training_video_url: Mapped[Optional[str]] = mapped_column(String(500))
