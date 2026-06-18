from typing import Optional
from sqlalchemy import String, Integer, Float, ForeignKey, JSON, Date
from sqlalchemy.orm import Mapped, mapped_column
from datetime import date

from ..db import Base
from ._base import IdMixin, TimestampMixin


class Plan(Base, IdMixin, TimestampMixin):
    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # silver|gold|platinum
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="AED")
    orders_cap: Mapped[Optional[int]] = mapped_column(Integer)         # null = unlimited
    conversations_cap: Mapped[Optional[int]] = mapped_column(Integer)
    stores_cap: Mapped[Optional[int]] = mapped_column(Integer)
    universal_numbers_cap: Mapped[Optional[int]] = mapped_column(Integer)
    features: Mapped[Optional[list]] = mapped_column(JSON, default=list)


class Usage(Base, IdMixin, TimestampMixin):
    __tablename__ = "usages"

    reseller_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resellers.id", ondelete="CASCADE"), index=True
    )
    cycle_start: Mapped[date] = mapped_column(Date, nullable=False)
    cycle_end: Mapped[date] = mapped_column(Date, nullable=False)
    orders_used: Mapped[int] = mapped_column(Integer, default=0)
    conversations_used: Mapped[int] = mapped_column(Integer, default=0)
