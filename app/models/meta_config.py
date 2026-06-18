from typing import Optional
from sqlalchemy import String, ForeignKey, Text
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
    verified: Mapped[bool] = mapped_column(default=False)
