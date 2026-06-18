from typing import Optional
from sqlalchemy import String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import IdMixin, TimestampMixin


class Template(Base, IdMixin, TimestampMixin):
    """WhatsApp message template. In production, status reflects Meta's
    approval state. For now we mock it (admin/UI can flip status)."""

    __tablename__ = "templates"

    reseller_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resellers.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(32), default="custom")  # return|delivered|confirmation|offer|custom
    language: Mapped[str] = mapped_column(String(16), default="en")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|rejected
    meta_template_name: Mapped[Optional[str]] = mapped_column(String(120))  # the name Meta has
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text)
