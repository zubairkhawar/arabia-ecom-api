from sqlalchemy import String, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import IdMixin, TimestampMixin


class AdminUser(Base, IdMixin, TimestampMixin):
    __tablename__ = "admin_users"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255))
    level: Mapped[str] = mapped_column(String(16), default="Admin")  # Owner|Admin|Support
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
