from typing import Optional
from sqlalchemy import String, Integer, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import IdMixin, TimestampMixin


class PoolNumber(Base, IdMixin, TimestampMixin):
    __tablename__ = "pool_numbers"

    number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    country: Mapped[str] = mapped_column(String(64), nullable=False)
    country_code: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    flag: Mapped[str] = mapped_column(String(8), default="🌍")
    capacity: Mapped[int] = mapped_column(Integer, default=50)
    assigned: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|full|disabled

    waba_id: Mapped[Optional[str]] = mapped_column(String(64))
    phone_number_id: Mapped[Optional[str]] = mapped_column(String(64))
    access_token_enc: Mapped[Optional[str]] = mapped_column(Text)


class PoolAssignment(Base, IdMixin, TimestampMixin):
    __tablename__ = "pool_assignments"

    reseller_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resellers.id", ondelete="CASCADE"), index=True, unique=True
    )
    pool_number_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("pool_numbers.id", ondelete="CASCADE"), index=True
    )
