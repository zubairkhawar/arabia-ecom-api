"""CSV import/export for orders.

Import schema (header row required):
  order_id, tracking_number, delivery_status

We only update existing orders. tracking_number / delivery_status overwrite.
Invalid rows are reported back.
"""
import csv
import io
from typing import List, Dict, Any
from sqlalchemy.orm import Session

from ..models import Order, Customer, OrderItem, Product, Reseller


VALID_DELIVERY = {"pending", "dispatched", "in_transit", "delivered", "returned", "failed"}


def parse_orders_import(content: bytes) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Returns (valid_rows, errors)."""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    valid: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for idx, row in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        oid = row.get("order_id")
        if not oid:
            errors.append({"row": idx, "error": "missing order_id"})
            continue
        ds = row.get("delivery_status") or None
        if ds and ds.lower() not in VALID_DELIVERY:
            errors.append({"row": idx, "error": f"invalid delivery_status '{ds}'"})
            continue
        valid.append({
            "order_id": oid,
            "tracking_number": row.get("tracking_number") or None,
            "delivery_status": (ds.lower() if ds else None),
        })
    return valid, errors


def apply_orders_import(
    db: Session, reseller: Reseller, rows: List[Dict[str, Any]]
) -> Dict[str, int]:
    updated = 0
    not_found = 0
    for r in rows:
        o = db.query(Order).filter(
            Order.code == r["order_id"], Order.reseller_id == reseller.id
        ).first()
        if not o:
            not_found += 1
            continue
        if r["tracking_number"]:
            o.tracking_number = r["tracking_number"]
        if r["delivery_status"]:
            o.delivery_status = r["delivery_status"]
        updated += 1
    db.commit()
    return {"updated": updated, "not_found": not_found, "total": len(rows)}


def export_orders_csv(db: Session, reseller: Reseller) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "order_id", "created_at", "status", "delivery_status",
        "tracking_number", "channel", "source_platform",
        "customer_name", "customer_phone", "amount", "currency",
        "items",
    ])
    orders = db.query(Order).filter(Order.reseller_id == reseller.id).order_by(Order.created_at.desc()).all()
    for o in orders:
        cust = db.get(Customer, o.customer_id)
        items_str = " | ".join(
            f"{it.qty}x {(db.get(Product, it.product_id) or type('x',(),{'name':it.product_id})).name}"
            for it in o.items
        )
        w.writerow([
            o.code,
            o.created_at.isoformat() if o.created_at else "",
            o.status,
            o.delivery_status,
            o.tracking_number or "",
            o.channel,
            o.source_platform or "",
            cust.name if cust else "",
            cust.phone if cust else "",
            f"{o.amount:.2f}",
            o.currency,
            items_str,
        ])
    return buf.getvalue().encode("utf-8")
