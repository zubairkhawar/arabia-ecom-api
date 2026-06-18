from typing import Optional
from sqlalchemy import String, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import IdMixin, TimestampMixin


class WhatsAppConfig(Base, IdMixin, TimestampMixin):
    """Per-reseller WhatsApp Cloud API config (when using own number).
    When number_type == 'universal' we ignore these and route to a PoolNumber instead."""

    __tablename__ = "whatsapp_configs"

    reseller_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resellers.id", ondelete="CASCADE"), unique=True
    )
    number_type: Mapped[str] = mapped_column(String(16), default="own")  # own | universal
    waba_id: Mapped[Optional[str]] = mapped_column(String(64))
    phone_number_id: Mapped[Optional[str]] = mapped_column(String(64))
    display_phone_number: Mapped[Optional[str]] = mapped_column(String(32))
    access_token_enc: Mapped[Optional[str]] = mapped_column(Text)
    webhook_verify_token: Mapped[Optional[str]] = mapped_column(String(128))
    verified: Mapped[bool] = mapped_column(default=False)
