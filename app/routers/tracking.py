from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select, func, case

from ..db import get_db
from ..deps import get_current_reseller
from ..models import Reseller, ClickSession, Order, Product

router = APIRouter(prefix="/tracking", tags=["tracking"])


class PlatformStats(BaseModel):
    platform: str
    clicks: int
    orders: int
    delivered: int
    returned: int
    conversion_rate: float
    return_rate: float


class ProductPlatformBreakdown(BaseModel):
    product_id: str
    product_name: str
    platform: str
    clicks: int
    orders: int
    delivered: int
    returned: int


class TrackingOverview(BaseModel):
    total_clicks: int
    total_orders: int
    delivered: int
    returned: int
    overall_conversion: float
    overall_return_rate: float
    by_platform: List[PlatformStats]
    by_product_platform: List[ProductPlatformBreakdown]


PLATFORMS = ["tiktok", "meta", "snapchat", "google", "other"]


@router.get("/overview", response_model=TrackingOverview)
def overview(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
    days: int = Query(7, ge=1, le=365),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    # platform clicks
    clicks_rows = db.execute(
        select(ClickSession.src_platform, func.count(ClickSession.id))
        .where(ClickSession.reseller_id == current.id, ClickSession.created_at >= since)
        .group_by(ClickSession.src_platform)
    ).all()
    clicks_map = {p: 0 for p in PLATFORMS}
    for src, n in clicks_rows:
        clicks_map[src or "other"] = n

    # platform orders / delivered / returned
    orders_rows = db.execute(
        select(
            Order.source_platform,
            func.count(Order.id),
            func.sum(case((Order.delivery_status == "delivered", 1), else_=0)),
            func.sum(case((Order.delivery_status == "returned", 1), else_=0)),
        )
        .where(Order.reseller_id == current.id, Order.created_at >= since)
        .group_by(Order.source_platform)
    ).all()
    orders_map = {p: {"orders": 0, "delivered": 0, "returned": 0} for p in PLATFORMS}
    for src, n, d, r in orders_rows:
        key = src or "other"
        if key not in orders_map:
            orders_map[key] = {"orders": 0, "delivered": 0, "returned": 0}
        orders_map[key]["orders"] = n or 0
        orders_map[key]["delivered"] = int(d or 0)
        orders_map[key]["returned"] = int(r or 0)

    by_platform: List[PlatformStats] = []
    total_clicks = sum(clicks_map.values())
    total_orders = sum(v["orders"] for v in orders_map.values())
    total_delivered = sum(v["delivered"] for v in orders_map.values())
    total_returned = sum(v["returned"] for v in orders_map.values())

    for p in PLATFORMS:
        c = clicks_map.get(p, 0)
        o = orders_map.get(p, {}).get("orders", 0)
        d = orders_map.get(p, {}).get("delivered", 0)
        r = orders_map.get(p, {}).get("returned", 0)
        conv = (o / c * 100.0) if c else 0.0
        ret = (r / o * 100.0) if o else 0.0
        by_platform.append(PlatformStats(
            platform=p, clicks=c, orders=o, delivered=d, returned=r,
            conversion_rate=round(conv, 2), return_rate=round(ret, 2),
        ))

    # product × platform breakdown
    rows = db.execute(
        select(
            Order.id, Order.source_platform, Order.delivery_status,
        )
        .where(Order.reseller_id == current.id)
    ).all()
    # collect product ids per order via OrderItem
    from ..models import OrderItem
    items = db.execute(
        select(OrderItem.order_id, OrderItem.product_id, Product.name)
        .join(Product, Product.id == OrderItem.product_id)
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.reseller_id == current.id)
    ).all()
    item_by_order = {}
    for oid, pid, pname in items:
        item_by_order.setdefault(oid, []).append((pid, pname))

    bucket = {}  # (pid, platform) -> {clicks, orders, delivered, returned, name}
    for oid, src, ds in rows:
        plat = src or "other"
        for pid, pname in item_by_order.get(oid, []):
            key = (pid, plat)
            b = bucket.setdefault(key, {"clicks": 0, "orders": 0, "delivered": 0, "returned": 0, "name": pname})
            b["orders"] += 1
            if ds == "delivered":
                b["delivered"] += 1
            if ds == "returned":
                b["returned"] += 1

    # clicks per (product, platform)
    click_rows = db.execute(
        select(ClickSession.product_id, ClickSession.src_platform, func.count(ClickSession.id))
        .where(ClickSession.reseller_id == current.id)
        .group_by(ClickSession.product_id, ClickSession.src_platform)
    ).all()
    for pid, src, n in click_rows:
        plat = src or "other"
        key = (pid, plat)
        if key not in bucket:
            p = db.get(Product, pid)
            bucket[key] = {"clicks": 0, "orders": 0, "delivered": 0, "returned": 0, "name": p.name if p else pid}
        bucket[key]["clicks"] = n

    by_product = [
        ProductPlatformBreakdown(
            product_id=pid, product_name=b["name"], platform=plat,
            clicks=b["clicks"], orders=b["orders"],
            delivered=b["delivered"], returned=b["returned"],
        )
        for (pid, plat), b in sorted(bucket.items(), key=lambda x: (-x[1]["orders"], -x[1]["clicks"]))
    ]

    overall_conv = (total_orders / total_clicks * 100.0) if total_clicks else 0.0
    overall_ret = (total_returned / total_orders * 100.0) if total_orders else 0.0

    return TrackingOverview(
        total_clicks=total_clicks,
        total_orders=total_orders,
        delivered=total_delivered,
        returned=total_returned,
        overall_conversion=round(overall_conv, 2),
        overall_return_rate=round(overall_ret, 2),
        by_platform=by_platform,
        by_product_platform=by_product,
    )
