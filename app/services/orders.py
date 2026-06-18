"""Order assembly + lifecycle."""
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from ..models import Order, OrderItem, Product, ProductVariant, Customer, Reseller, Chat, ClickSession
from .pricing import quote_order, LineRequest
from .attribution import dispatch_purchase


def gen_order_code(db: Session) -> str:
    count = db.execute(select(func.count(Order.id))).scalar_one() or 0
    return f"ORD-{2000 + count + 1}"


def find_or_create_customer(
    db: Session, reseller: Reseller, phone: str, name: Optional[str] = None
) -> Customer:
    c = db.execute(
        select(Customer).where(Customer.reseller_id == reseller.id, Customer.phone == phone)
    ).scalar_one_or_none()
    if c:
        if name and not c.name:
            c.name = name
        return c
    c = Customer(reseller_id=reseller.id, phone=phone, name=name)
    db.add(c)
    db.flush()
    return c


def create_order_from_items(
    db: Session,
    reseller: Reseller,
    customer: Customer,
    items: List[Dict[str, Any]],
    chat: Optional[Chat] = None,
    address: Optional[str] = None,
    source: str = "manual",
) -> Order:
    """`items` shape: [{product_id, variant_id?, qty}]"""
    line_reqs = []
    for it in items:
        p = db.get(Product, it["product_id"])
        if not p:
            continue
        v = None
        if it.get("variant_id"):
            v = db.get(ProductVariant, it["variant_id"])
        line_reqs.append(LineRequest(product=p, variant=v, qty=int(it["qty"])))

    if not line_reqs:
        raise ValueError("No valid items to order")

    quote = quote_order(line_reqs, currency=reseller.currency)

    click_id = chat.click_session_id if chat else None
    src_platform = None
    if click_id:
        cs = db.get(ClickSession, click_id)
        if cs:
            src_platform = cs.src_platform

    order = Order(
        reseller_id=reseller.id,
        customer_id=customer.id,
        chat_id=chat.id if chat else None,
        click_session_id=click_id,
        code=gen_order_code(db),
        amount=quote.subtotal,
        currency=quote.currency,
        channel="whatsapp",
        status="processing",
        delivery_status="pending",
        source=source,
        source_platform=src_platform,
        customer_address={"raw": address} if address else None,
    )
    db.add(order)
    db.flush()

    for ql in quote.lines:
        db.add(OrderItem(
            order_id=order.id,
            product_id=ql.product_id,
            variant_id=ql.variant_id,
            qty=ql.qty,
            unit_price=ql.unit_price,
            line_total=ql.line_total,
        ))

    db.flush()
    return order


async def confirm_order(db: Session, reseller: Reseller, order: Order) -> Order:
    """Mark the order confirmed, increment customer totals, fire Purchase CAPI."""
    if order.status == "confirmed":
        return order
    order.status = "confirmed"
    order.confirmed_at = datetime.now(timezone.utc)
    cust = db.get(Customer, order.customer_id)
    if cust:
        cust.total_orders = (cust.total_orders or 0) + 1
        cust.total_spent = (cust.total_spent or 0.0) + order.amount
    db.flush()
    await dispatch_purchase(db, reseller, order)
    db.commit()
    return order
