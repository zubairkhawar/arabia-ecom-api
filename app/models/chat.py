from typing import Optional, List
from sqlalchemy import String, Integer, ForeignKey, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ._base import IdMixin, TimestampMixin


class Chat(Base, IdMixin, TimestampMixin):
    __tablename__ = "chats"

    reseller_id: Mapped[str] = mapped_column(String(32), ForeignKey("resellers.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[str] = mapped_column(String(32), ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    click_session_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("click_sessions.id", ondelete="SET NULL"))

    channel: Mapped[str] = mapped_column(String(16), default="whatsapp")
    mode: Mapped[str] = mapped_column(String(16), default="ai")  # ai | human
    unread: Mapped[int] = mapped_column(Integer, default=0)
    draft_items: Mapped[Optional[list]] = mapped_column(JSON, default=list)  # [{product_id, variant_id, qty}]
    wa_thread_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)  # customer WA phone

    messages: Mapped[List["Message"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(Base, IdMixin, TimestampMixin):
    __tablename__ = "messages"

    chat_id: Mapped[str] = mapped_column(String(32), ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    sender: Mapped[str] = mapped_column(String(16))  # customer | ai | human
    text: Mapped[str] = mapped_column(Text, nullable=False)
    wa_message_id: Mapped[Optional[str]] = mapped_column(String(128))

    chat: Mapped["Chat"] = relationship(back_populates="messages")
