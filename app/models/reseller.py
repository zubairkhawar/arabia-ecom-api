from typing import Optional
from sqlalchemy import String, Boolean, Integer, Text, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ._base import IdMixin, TimestampMixin


class Reseller(Base, IdMixin, TimestampMixin):
    __tablename__ = "resellers"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="silver")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    country: Mapped[str] = mapped_column(String(8), nullable=False, default="UAE")
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="AED")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="reseller")  # reseller | admin

    ai_settings: Mapped[Optional["AISetting"]] = relationship(
        back_populates="reseller", cascade="all, delete-orphan", uselist=False
    )


class AISetting(Base, IdMixin, TimestampMixin):
    __tablename__ = "ai_settings"

    reseller_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resellers.id", ondelete="CASCADE"), unique=True
    )
    ai_name: Mapped[str] = mapped_column(String(64), default="Max")
    role: Mapped[str] = mapped_column(Text, default="Friendly sales assistant")
    tone: Mapped[str] = mapped_column(String(32), default="Friendly")
    creativity: Mapped[int] = mapped_column(Integer, default=65)
    response_length: Mapped[str] = mapped_column(String(16), default="Medium")
    primary_language: Mapped[str] = mapped_column(String(32), default="English")
    supported_languages: Mapped[list] = mapped_column(JSON, default=lambda: ["English"])
    always_sound_human: Mapped[bool] = mapped_column(Boolean, default=True)
    upsell_aggressiveness: Mapped[int] = mapped_column(Integer, default=40)
    convince_hesitant: Mapped[bool] = mapped_column(Boolean, default=True)
    fallback_to_human: Mapped[bool] = mapped_column(Boolean, default=True)
    business_hours: Mapped[Optional[list]] = mapped_column(JSON, default=list)

    reseller: Mapped["Reseller"] = relationship(back_populates="ai_settings")
