from typing import Optional
from sqlalchemy import String, ForeignKey, Text, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import IdMixin, TimestampMixin


class Notification(Base, IdMixin, TimestampMixin):
    """In-app notifications surfaced via the bell dropdown.

    type:
      new_chat            — first inbound message on a new conversation
      new_message         — incoming message on an existing chat
      escalation          — customer asked for a real agent
      order_confirmed     — AI just confirmed an order
      system              — anything else
    """

    __tablename__ = "notifications"

    # Who sees it. Resellers see their own; admin sees all.
    reseller_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("resellers.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[Optional[str]] = mapped_column(Text)
    # Deep-link target inside the app (e.g. /reseller/chats?focus=<chat_id>)
    href: Mapped[Optional[str]] = mapped_column(String(255))
    # Extra context: chat_id, customer_phone, order_id, etc.
    meta: Mapped[Optional[dict]] = mapped_column(JSON)
    read_at: Mapped[Optional[str]] = mapped_column(String(40))
    seen: Mapped[bool] = mapped_column(Boolean, default=False)
