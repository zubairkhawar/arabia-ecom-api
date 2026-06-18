from typing import List, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select

from ..db import get_db
from ..deps import get_current_reseller
from ..models import (
    Reseller, Order, OrderItem, Customer, Product, ProductVariant, Template, WhatsAppConfig
)
from ..services.orders import find_or_create_customer, create_order_from_items, confirm_order
from ..services.csv_io import parse_orders_import, apply_orders_import, export_orders_csv, VALID_DELIVERY
from ..services.whatsapp_cloud import send_text
from ..schemas.orders import (
    OrderOut, OrderItemOut, OrderUpdate, OrderCreate, FollowUpSend
)


router = APIRouter(prefix="/orders", tags=["orders"])


def _serialize(db: Session, o: Order) -> OrderOut:
    cust = db.get(Customer, o.customer_id)
    items = []
    for it in o.items:
        p = db.get(Product, it.product_id)
        v = db.get(ProductVariant, it.variant_id) if it.variant_id else None
        items.append(OrderItemOut(
            id=it.id, product_id=it.product_id,
            product_name=p.name if p else None,
            variant_id=it.variant_id,
            variant_label=v.label if v else None,
            qty=it.qty, unit_price=it.unit_price, line_total=it.line_total,
        ))
    return OrderOut(
        id=o.id, code=o.code, reseller_id=o.reseller_id, customer_id=o.customer_id,
        customer_name=cust.name if cust else None,
        customer_phone=cust.phone if cust else None,
        chat_id=o.chat_id, click_session_id=o.click_session_id,
        amount=o.amount, currency=o.currency, channel=o.channel,
        status=o.status, delivery_status=o.delivery_status,
        tracking_number=o.tracking_number, source=o.source,
        source_platform=o.source_platform,
        follow_up_template_id=o.follow_up_template_id,
        follow_up_sent_at=o.follow_up_sent_at,
        purchase_event_sent=o.purchase_event_sent,
        items=items,
        customer_address=o.customer_address,
        created_at=o.created_at,
        confirmed_at=o.confirmed_at,
    )


@router.get("", response_model=List[OrderOut])
def list_orders(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
    channel: Optional[str] = None,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    delivery_status: Optional[str] = None,
    src: Optional[str] = None,
    q: Optional[str] = None,
):
    stmt = select(Order).where(Order.reseller_id == current.id).order_by(Order.created_at.desc())
    if channel:
        stmt = stmt.where(Order.channel == channel)
    if status_filter:
        stmt = stmt.where(Order.status == status_filter)
    if delivery_status:
        stmt = stmt.where(Order.delivery_status == delivery_status)
    if src:
        stmt = stmt.where(Order.source_platform == src)
    rows = db.execute(stmt).scalars().all()
    if q:
        ql = q.lower()
        kept = []
        for o in rows:
            cust = db.get(Customer, o.customer_id)
            hay = f"{o.code} {cust.name if cust else ''} {cust.phone if cust else ''}".lower()
            if ql in hay:
                kept.append(o)
        rows = kept
    return [_serialize(db, o) for o in rows]


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
async def create_order(
    payload: OrderCreate,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    cust = find_or_create_customer(db, current, payload.customer_phone, payload.customer_name)
    items_data = [{"product_id": l.product_id, "variant_id": l.variant_id, "qty": l.qty} for l in payload.items]
    o = create_order_from_items(
        db, current, cust, items_data, address=payload.address,
        source=payload.source or "manual",
    )
    if payload.confirm:
        await confirm_order(db, current, o)
    else:
        db.commit()
    db.refresh(o)
    return _serialize(db, o)


@router.get("/{order_id}", response_model=OrderOut)
def get_order(
    order_id: str,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    o = db.get(Order, order_id)
    if not o or o.reseller_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")
    return _serialize(db, o)


@router.patch("/{order_id}", response_model=OrderOut)
async def update_order(
    order_id: str,
    payload: OrderUpdate,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    o = db.get(Order, order_id)
    if not o or o.reseller_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")

    data = payload.model_dump(exclude_unset=True)
    if "delivery_status" in data and data["delivery_status"]:
        if data["delivery_status"] not in VALID_DELIVERY:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"invalid delivery_status (allowed: {sorted(VALID_DELIVERY)})")
    if "status" in data and data["status"]:
        if data["status"] not in ("processing", "confirmed", "hold", "cancelled"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "status must be processing|confirmed|hold|cancelled")
        was_confirmed = o.status == "confirmed"
        for k, v in data.items():
            setattr(o, k, v)
        if not was_confirmed and o.status == "confirmed":
            # transition → fire Purchase CAPI
            await confirm_order(db, current, o)
        else:
            db.commit()
    else:
        for k, v in data.items():
            setattr(o, k, v)
        db.commit()
    db.refresh(o)
    return _serialize(db, o)


@router.post("/{order_id}/follow-up")
async def send_follow_up(
    order_id: str,
    payload: FollowUpSend,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    o = db.get(Order, order_id)
    if not o or o.reseller_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")
    tpl = db.get(Template, payload.template_id)
    if not tpl or tpl.reseller_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template not found")
    if tpl.status != "approved":
        raise HTTPException(status.HTTP_409_CONFLICT, "template is not approved")
    cust = db.get(Customer, o.customer_id)
    if not cust:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer not found")
    body = (tpl.body or "").replace("{customer_name}", cust.name or "")
    body = body.replace("{product}", o.items[0].product_id if o.items else "")
    body = body.replace("{order_id}", o.code)
    body = body.replace("{tracking_number}", o.tracking_number or "")
    cfg = db.execute(
        select(WhatsAppConfig).where(WhatsAppConfig.reseller_id == current.id)
    ).scalar_one_or_none()
    result = await send_text(
        cfg.phone_number_id if cfg else None,
        cfg.access_token_enc if cfg else None,
        cust.phone, body,
    )
    o.follow_up_template_id = tpl.id
    o.follow_up_sent_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "sent_at": o.follow_up_sent_at, "wa": result}


# ---------- CSV ----------

@router.get("/export/csv")
def export_csv(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    data = export_orders_csv(db, current)
    return Response(
        content=data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=orders.csv"},
    )


@router.post("/import/csv")
async def import_csv(
    file: UploadFile = File(...),
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    content = await file.read()
    rows, errors = parse_orders_import(content)
    summary = apply_orders_import(db, current, rows)
    return {"ok": True, **summary, "errors": errors}
