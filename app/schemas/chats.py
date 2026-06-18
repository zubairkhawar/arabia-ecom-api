from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime


class MessageOut(BaseModel):
    id: str
    sender: str
    text: str
    created_at: datetime


class ChatSummary(BaseModel):
    id: str
    customer_id: str
    customer_name: Optional[str]
    customer_phone: str
    channel: str
    mode: str
    unread: int
    last_message: Optional[str]
    last_message_at: Optional[datetime]


class ChatDetail(BaseModel):
    id: str
    customer_id: str
    customer_name: Optional[str]
    customer_phone: str
    customer_location: Optional[str]
    customer_total_orders: int
    customer_total_spent: float
    channel: str
    mode: str
    click_session_id: Optional[str]
    src_platform: Optional[str]
    draft_items: List[dict]
    messages: List[MessageOut]


class ModeChange(BaseModel):
    mode: str  # 'ai' or 'human'


class HumanReply(BaseModel):
    text: str
