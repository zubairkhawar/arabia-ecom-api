from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select, func, case

from ..config import settings
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


class LinkSourceBreakdown(BaseModel):
    platform: str
    clicks: int
    orders: int


class LinkRow(BaseModel):
    product_id: str
    product_name: str
    slug: str
    generated_url: str
    image_url: Optional[str]
    clicks: int
    orders: int
    delivered: int
    returned: int
    conversion_rate: float
    delivery_rate: float
    by_source: List[LinkSourceBreakdown]


class TrackingLinksOut(BaseModel):
    rows: List[LinkRow]
    window_days: int
    total_clicks: int
    total_orders: int
    total_delivered: int
    total_returned: int
    total_orders_unattributed: int


@router.get("/links", response_model=TrackingLinksOut)
def links(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
    days: int = Query(30, ge=1, le=365),
):
    """Per-link performance: one row per product, aggregated by the
    ClickSession the order was attributed to (strict — orders without
    click_session_id are excluded from rows and surfaced separately as
    total_orders_unattributed)."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Click counts per (product, platform) — source of truth for clicks.
    click_rows = db.execute(
        select(
            ClickSession.product_id,
            ClickSession.src_platform,
            func.count(ClickSession.id),
        )
        .where(
            ClickSession.reseller_id == current.id,
            ClickSession.created_at >= since,
        )
        .group_by(ClickSession.product_id, ClickSession.src_platform)
    ).all()

    # Attributed orders per (link-product, platform). Strict join via
    # Order.click_session_id; orders without a click are excluded here and
    # counted separately below. We group by ClickSession.product_id (the link
    # clicked), NOT OrderItem.product_id — link performance measures
    # click-intent, not basket contents.
    order_rows = db.execute(
        select(
            ClickSession.product_id,
            ClickSession.src_platform,
            func.count(Order.id),
            func.sum(case((Order.delivery_status == "delivered", 1), else_=0)),
            func.sum(case((Order.delivery_status == "returned", 1), else_=0)),
        )
        .join(ClickSession, ClickSession.id == Order.click_session_id)
        .where(
            Order.reseller_id == current.id,
            Order.created_at >= since,
        )
        .group_by(ClickSession.product_id, ClickSession.src_platform)
    ).all()

    # Orders we couldn't attribute to any link — surfaced in the header
    # so resellers can see the gap rather than have it silently dropped.
    total_orders_unattributed = db.execute(
        select(func.count(Order.id)).where(
            Order.reseller_id == current.id,
            Order.created_at >= since,
            Order.click_session_id.is_(None),
        )
    ).scalar_one()

    # Fold into a nested map: pid -> { platform -> {clicks, orders, delivered, returned} }
    by_pid_platform: dict[str, dict[str, dict[str, int]]] = {}

    def _bucket(pid: str, plat: str) -> dict[str, int]:
        return by_pid_platform.setdefault(pid, {}).setdefault(
            plat, {"clicks": 0, "orders": 0, "delivered": 0, "returned": 0}
        )

    for pid, plat, n in click_rows:
        _bucket(pid, plat or "other")["clicks"] = n or 0

    for pid, plat, n, d, r in order_rows:
        b = _bucket(pid, plat or "other")
        b["orders"] = n or 0
        b["delivered"] = int(d or 0)
        b["returned"] = int(r or 0)

    # Resolve product metadata for the products that appeared in either
    # query; missing products (deleted/inactive) get filtered out.
    pids = list(by_pid_platform.keys())
    products: dict[str, Product] = {}
    if pids:
        prows = db.execute(
            select(Product).where(
                Product.id.in_(pids), Product.reseller_id == current.id
            )
        ).scalars().all()
        products = {p.id: p for p in prows}

    rows: List[LinkRow] = []
    for pid, by_plat in by_pid_platform.items():
        p = products.get(pid)
        if not p:
            continue

        by_source = [
            LinkSourceBreakdown(
                platform=plat,
                clicks=by_plat.get(plat, {}).get("clicks", 0),
                orders=by_plat.get(plat, {}).get("orders", 0),
            )
            for plat in PLATFORMS
        ]

        clicks = sum(s.clicks for s in by_source)
        orders = sum(s.orders for s in by_source)
        delivered = sum(v.get("delivered", 0) for v in by_plat.values())
        returned = sum(v.get("returned", 0) for v in by_plat.values())
        conv = (orders / clicks * 100.0) if clicks else 0.0
        delivery_rate = (delivered / orders * 100.0) if orders else 0.0

        rows.append(LinkRow(
            product_id=p.id,
            product_name=p.name,
            slug=p.slug,
            generated_url=f"{settings.link_domain}/r/{p.slug}",
            image_url=p.image_url,
            clicks=clicks,
            orders=orders,
            delivered=delivered,
            returned=returned,
            conversion_rate=round(conv, 2),
            delivery_rate=round(delivery_rate, 2),
            by_source=by_source,
        ))

    rows.sort(key=lambda r: (-r.orders, -r.clicks, r.product_name.lower()))

    return TrackingLinksOut(
        rows=rows,
        window_days=days,
        total_clicks=sum(r.clicks for r in rows),
        total_orders=sum(r.orders for r in rows),
        total_delivered=sum(r.delivered for r in rows),
        total_returned=sum(r.returned for r in rows),
        total_orders_unattributed=int(total_orders_unattributed or 0),
    )
