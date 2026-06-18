from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from datetime import datetime


class OrderItemOut(BaseModel):
    id: str
    product_id: str
    product_name: Optional[str] = None
    variant_id: Optional[str]
    variant_label: Optional[str] = None
    qty: int
    unit_price: float
    line_total: float


class OrderOut(BaseModel):
    id: str
    code: str
    reseller_id: str
    customer_id: str
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    chat_id: Optional[str]
    click_session_id: Optional[str]
    amount: float
    currency: str
    channel: str
    status: str
    delivery_status: str
    tracking_number: Optional[str]
    source: Optional[str]
    source_platform: Optional[str]
    follow_up_template_id: Optional[str]
    follow_up_sent_at: Optional[datetime]
    purchase_event_sent: bool
    items: List[OrderItemOut]
    customer_address: Optional[Dict[str, Any]]
    created_at: datetime
    confirmed_at: Optional[datetime]


class OrderUpdate(BaseModel):
    status: Optional[str] = None
    delivery_status: Optional[str] = None
    tracking_number: Optional[str] = None
    customer_address: Optional[Dict[str, Any]] = None


class OrderCreateLine(BaseModel):
    product_id: str
    variant_id: Optional[str] = None
    qty: int = 1


class OrderCreate(BaseModel):
    customer_phone: str
    customer_name: Optional[str] = None
    items: List[OrderCreateLine]
    address: Optional[str] = None
    confirm: bool = False
    source: Optional[str] = "manual"


class FollowUpSend(BaseModel):
    template_id: str
