"""Dashboard aggregates — replaces the frontend's mock data."""
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func, case
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_reseller, require_admin
from ..models import (
    Reseller, Order, OrderItem, Product, Chat, Customer,
    ClickSession, MetaConfig, WhatsAppConfig, PoolNumber,
)


router = APIRouter(tags=["dashboard"])


# ---------------- shapes ----------------


class StatDelta(BaseModel):
    value: float
    delta: float  # % change vs the previous window
    direction: str  # 'up' | 'down' | 'flat'


class SeriesPoint(BaseModel):
    day: str
    conversations: int
    orders: int


class StatusSlice(BaseModel):
    name: str
    value: int
    color: str


class TopProduct(BaseModel):
    product_id: str
    name: str
    image: Optional[str]
    orders: int
    trend: float


class RecentChat(BaseModel):
    chat_id: str
    customer_id: str
    customer_name: Optional[str]
    customer_phone: str
    last_message: Optional[str]
    last_message_at: Optional[datetime]
    unread: int
    mode: str


class AIPerformance(BaseModel):
    success_rate: float
    handled_by_ai: StatDelta
    human_takeover: StatDelta


class OnboardingStep(BaseModel):
    label: str
    done: bool


class DashboardOut(BaseModel):
    stats: dict  # totalConversations / ordersCreated / confirmedOrders / conversionRate / revenue
    series: List[SeriesPoint]
    order_status: List[StatusSlice]
    top_products: List[TopProduct]
    recent_chats: List[RecentChat]
    ai_performance: AIPerformance
    onboarding: List[OnboardingStep]
    currency: str


class AdminStatsOut(BaseModel):
    total_resellers: StatDelta
    active_whatsapp: StatDelta
    active_shopify: StatDelta
    total_conversations: StatDelta
    total_orders: StatDelta
    platform_revenue: StatDelta
    ai_success_rate: StatDelta
    pool_utilization: List[dict]
    top_resellers: List[dict]


# ---------------- helpers ----------------


def _delta(curr: float, prev: float) -> StatDelta:
    if prev == 0:
        d = 100.0 if curr > 0 else 0.0
    else:
        d = ((curr - prev) / prev) * 100.0
    direction = "up" if d > 0 else ("down" if d < 0 else "flat")
    return StatDelta(value=float(curr), delta=round(d, 2), direction=direction)


def _window(now: datetime, days: int = 7) -> tuple[datetime, datetime, datetime]:
    """Return (current_start, prev_start, prev_end). Current = last `days`,
    previous = the `days` before that."""
    end = now
    start = end - timedelta(days=days)
    prev_end = start
    prev_start = start - timedelta(days=days)
    return start, prev_start, prev_end


def _empty_chart(now: datetime, days: int) -> List[SeriesPoint]:
    """Build an empty time-series with `days` buckets ending at `now`.
    Bucket size adapts: ≤14d = daily, ≤90d = daily, 365d = weekly."""
    # For longer ranges we use weekly buckets so we don't return 365 points
    if days >= 180:
        weeks = max(1, days // 7)
        out = []
        for i in range(weeks - 1, -1, -1):
            d = now - timedelta(days=i * 7)
            out.append(SeriesPoint(day=d.strftime("%b %d"), conversations=0, orders=0))
        return out
    fmt = "%a" if days <= 14 else "%b %d"
    return [
        SeriesPoint(day=(now - timedelta(days=i)).strftime(fmt), conversations=0, orders=0)
        for i in range(days - 1, -1, -1)
    ]


# ---------------- /me/dashboard ----------------


@router.get("/me/dashboard", response_model=DashboardOut)
def reseller_dashboard(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
    days: int = Query(7, ge=1, le=365, description="Window size in days"),
):
    now = datetime.now(timezone.utc)
    start, prev_start, prev_end = _window(now, days)

    # --- stat: conversations (chats created in window) ---
    convo_curr = db.execute(
        select(func.count(Chat.id)).where(
            Chat.reseller_id == current.id, Chat.created_at >= start
        )
    ).scalar_one()
    convo_prev = db.execute(
        select(func.count(Chat.id)).where(
            Chat.reseller_id == current.id,
            Chat.created_at >= prev_start, Chat.created_at < prev_end,
        )
    ).scalar_one()

    # --- stat: orders created ---
    orders_curr = db.execute(
        select(func.count(Order.id)).where(
            Order.reseller_id == current.id, Order.created_at >= start
        )
    ).scalar_one()
    orders_prev = db.execute(
        select(func.count(Order.id)).where(
            Order.reseller_id == current.id,
            Order.created_at >= prev_start, Order.created_at < prev_end,
        )
    ).scalar_one()

    # --- stat: confirmed orders ---
    confirmed_curr = db.execute(
        select(func.count(Order.id)).where(
            Order.reseller_id == current.id, Order.status == "confirmed",
            Order.created_at >= start,
        )
    ).scalar_one()
    confirmed_prev = db.execute(
        select(func.count(Order.id)).where(
            Order.reseller_id == current.id, Order.status == "confirmed",
            Order.created_at >= prev_start, Order.created_at < prev_end,
        )
    ).scalar_one()

    # --- stat: revenue (confirmed only) ---
    revenue_curr = float(db.execute(
        select(func.coalesce(func.sum(Order.amount), 0.0)).where(
            Order.reseller_id == current.id, Order.status == "confirmed",
            Order.created_at >= start,
        )
    ).scalar_one())
    revenue_prev = float(db.execute(
        select(func.coalesce(func.sum(Order.amount), 0.0)).where(
            Order.reseller_id == current.id, Order.status == "confirmed",
            Order.created_at >= prev_start, Order.created_at < prev_end,
        )
    ).scalar_one())

    # --- stat: conversion rate (orders / conversations) ---
    conv_rate_curr = (orders_curr / convo_curr * 100.0) if convo_curr else 0.0
    conv_rate_prev = (orders_prev / convo_prev * 100.0) if convo_prev else 0.0

    stats = {
        "totalConversations": _delta(convo_curr, convo_prev).model_dump(),
        "ordersCreated": _delta(orders_curr, orders_prev).model_dump(),
        "confirmedOrders": _delta(confirmed_curr, confirmed_prev).model_dump(),
        "conversionRate": _delta(round(conv_rate_curr, 2), round(conv_rate_prev, 2)).model_dump(),
        "revenue": {
            **_delta(revenue_curr, revenue_prev).model_dump(),
            "currency": current.currency,
        },
    }

    # --- time-series for the selected range ---
    # ONE query per series via date_trunc, then merge into the bucket array.
    chart = _empty_chart(now, days)
    bucket_days = 7 if days >= 180 else 1
    n_buckets = len(chart)
    series_start = (now - timedelta(days=n_buckets * bucket_days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    trunc = "week" if bucket_days == 7 else "day"

    chat_counts = dict(db.execute(
        select(
            func.date_trunc(trunc, Chat.created_at).label("b"),
            func.count(Chat.id),
        ).where(
            Chat.reseller_id == current.id, Chat.created_at >= series_start
        ).group_by("b")
    ).all())
    order_counts = dict(db.execute(
        select(
            func.date_trunc(trunc, Order.created_at).label("b"),
            func.count(Order.id),
        ).where(
            Order.reseller_id == current.id, Order.created_at >= series_start
        ).group_by("b")
    ).all())

    for i, p in enumerate(chart):
        bucket_end = (now - timedelta(days=(n_buckets - 1 - i) * bucket_days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(days=bucket_days)
        bucket_start = bucket_end - timedelta(days=bucket_days)
        # Postgres date_trunc returns a timestamp aligned to the bucket start.
        # We need to find that exact timestamp in the dicts.
        key = bucket_start if bucket_days == 1 else (
            bucket_start - timedelta(days=bucket_start.weekday())  # week starts Monday
        )
        # Try both possible alignments (UTC date_trunc vs computed)
        p.conversations = int(chat_counts.get(key, chat_counts.get(bucket_start, 0)) or 0)
        p.orders = int(order_counts.get(key, order_counts.get(bucket_start, 0)) or 0)

    # --- order status breakdown ---
    rows = db.execute(
        select(Order.status, func.count(Order.id)).where(
            Order.reseller_id == current.id, Order.created_at >= start,
        ).group_by(Order.status)
    ).all()
    status_map = {s: n for s, n in rows}
    status_colors = {
        "confirmed": "#10B981",
        "hold": "#F59E0B",
        "processing": "#3B82F6",
        "cancelled": "#EF4444",
    }
    order_status = [
        StatusSlice(name=k.title(), value=status_map.get(k, 0), color=status_colors[k])
        for k in ("confirmed", "hold", "processing", "cancelled")
    ]

    # --- top products by order count in window ---
    top_rows = db.execute(
        select(OrderItem.product_id, func.coalesce(func.sum(OrderItem.qty), 0).label("units"))
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.reseller_id == current.id, Order.created_at >= start)
        .group_by(OrderItem.product_id)
        .order_by(func.sum(OrderItem.qty).desc())
        .limit(5)
    ).all()
    top_products: List[TopProduct] = []
    for pid, units in top_rows:
        p = db.get(Product, pid)
        if not p:
            continue
        # naive trend %: units_curr vs units_prev for this product
        prev_units = db.execute(
            select(func.coalesce(func.sum(OrderItem.qty), 0))
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                Order.reseller_id == current.id,
                OrderItem.product_id == pid,
                Order.created_at >= prev_start, Order.created_at < prev_end,
            )
        ).scalar_one() or 0
        trend = 0.0
        if prev_units:
            trend = round(((int(units) - int(prev_units)) / int(prev_units)) * 100.0, 1)
        elif int(units) > 0:
            trend = 100.0
        top_products.append(TopProduct(
            product_id=pid, name=p.name, image=p.image_url,
            orders=int(units), trend=trend,
        ))

    # --- recent chats ---
    recent_chat_rows = db.execute(
        select(Chat).where(Chat.reseller_id == current.id)
        .order_by(Chat.updated_at.desc()).limit(5)
    ).scalars().all()
    recent_chats: List[RecentChat] = []
    for c in recent_chat_rows:
        cust = db.get(Customer, c.customer_id)
        last = c.messages[-1] if c.messages else None
        recent_chats.append(RecentChat(
            chat_id=c.id, customer_id=c.customer_id,
            customer_name=cust.name if cust else None,
            customer_phone=cust.phone if cust else "",
            last_message=last.text if last else None,
            last_message_at=last.created_at if last else None,
            unread=c.unread, mode=c.mode,
        ))

    # --- AI performance ---
    ai_curr = db.execute(
        select(func.count(Chat.id)).where(
            Chat.reseller_id == current.id, Chat.mode == "ai",
            Chat.created_at >= start,
        )
    ).scalar_one()
    human_curr = db.execute(
        select(func.count(Chat.id)).where(
            Chat.reseller_id == current.id, Chat.mode == "human",
            Chat.created_at >= start,
        )
    ).scalar_one()
    ai_prev = db.execute(
        select(func.count(Chat.id)).where(
            Chat.reseller_id == current.id, Chat.mode == "ai",
            Chat.created_at >= prev_start, Chat.created_at < prev_end,
        )
    ).scalar_one()
    human_prev = db.execute(
        select(func.count(Chat.id)).where(
            Chat.reseller_id == current.id, Chat.mode == "human",
            Chat.created_at >= prev_start, Chat.created_at < prev_end,
        )
    ).scalar_one()
    total = ai_curr + human_curr
    success_rate = round((ai_curr / total) * 100, 1) if total else 0.0

    # --- onboarding state (real) ---
    has_wa = db.execute(
        select(WhatsAppConfig.verified).where(WhatsAppConfig.reseller_id == current.id)
    ).scalar_one_or_none() or False
    meta_cfg = db.execute(
        select(MetaConfig).where(MetaConfig.reseller_id == current.id)
    ).scalar_one_or_none()
    has_product = db.execute(
        select(func.count(Product.id)).where(Product.reseller_id == current.id)
    ).scalar_one() > 0
    has_chat = db.execute(
        select(func.count(Chat.id)).where(Chat.reseller_id == current.id)
    ).scalar_one() > 0

    onboarding = [
        OnboardingStep(label="Account Created", done=True),
        OnboardingStep(label="Business Information", done=bool(current.name and current.country)),
        OnboardingStep(label="WhatsApp Number Connected", done=bool(has_wa)),
        OnboardingStep(label="Meta Pixel Connected", done=bool(meta_cfg and meta_cfg.is_capi_verified)),
        OnboardingStep(label="Product Added", done=has_product),
        OnboardingStep(label="AI Training", done=True),  # defaults applied at signup
        OnboardingStep(label="First Customer Chat", done=has_chat),
    ]

    return DashboardOut(
        stats=stats,
        series=chart,
        order_status=order_status,
        top_products=top_products,
        recent_chats=recent_chats,
        ai_performance=AIPerformance(
            success_rate=success_rate,
            handled_by_ai=_delta(ai_curr, ai_prev),
            human_takeover=_delta(human_curr, human_prev),
        ),
        onboarding=onboarding,
        currency=current.currency,
    )


# ---------------- /admin/stats ----------------


@router.get("/admin/stats", response_model=AdminStatsOut)
def admin_stats(
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
    days: int = Query(7, ge=1, le=365),
):
    now = datetime.now(timezone.utc)
    start, prev_start, prev_end = _window(now, days)

    total_resellers_curr = db.execute(select(func.count(Reseller.id))).scalar_one()
    total_resellers_prev = db.execute(
        select(func.count(Reseller.id)).where(Reseller.created_at < start)
    ).scalar_one()

    active_wa = db.execute(
        select(func.count(WhatsAppConfig.id)).where(WhatsAppConfig.verified == True)
    ).scalar_one()
    active_shop = 0  # Shopify not connected in Phase 1; placeholder

    convo_curr = db.execute(
        select(func.count(Chat.id)).where(Chat.created_at >= start)
    ).scalar_one()
    convo_prev = db.execute(
        select(func.count(Chat.id)).where(
            Chat.created_at >= prev_start, Chat.created_at < prev_end
        )
    ).scalar_one()

    orders_curr = db.execute(
        select(func.count(Order.id)).where(Order.created_at >= start)
    ).scalar_one()
    orders_prev = db.execute(
        select(func.count(Order.id)).where(
            Order.created_at >= prev_start, Order.created_at < prev_end
        )
    ).scalar_one()

    revenue_curr = float(db.execute(
        select(func.coalesce(func.sum(Order.amount), 0.0)).where(
            Order.status == "confirmed", Order.created_at >= start,
        )
    ).scalar_one())
    revenue_prev = float(db.execute(
        select(func.coalesce(func.sum(Order.amount), 0.0)).where(
            Order.status == "confirmed",
            Order.created_at >= prev_start, Order.created_at < prev_end,
        )
    ).scalar_one())

    ai_curr = db.execute(
        select(func.count(Chat.id)).where(Chat.mode == "ai", Chat.created_at >= start)
    ).scalar_one()
    total_chats_curr = db.execute(
        select(func.count(Chat.id)).where(Chat.created_at >= start)
    ).scalar_one()
    ai_rate_curr = round((ai_curr / total_chats_curr) * 100, 1) if total_chats_curr else 0.0

    ai_prev = db.execute(
        select(func.count(Chat.id)).where(
            Chat.mode == "ai",
            Chat.created_at >= prev_start, Chat.created_at < prev_end,
        )
    ).scalar_one()
    total_chats_prev = db.execute(
        select(func.count(Chat.id)).where(
            Chat.created_at >= prev_start, Chat.created_at < prev_end,
        )
    ).scalar_one()
    ai_rate_prev = round((ai_prev / total_chats_prev) * 100, 1) if total_chats_prev else 0.0

    # pool utilization rollup
    pool_rows = db.execute(
        select(PoolNumber.country_code,
               func.sum(PoolNumber.assigned), func.sum(PoolNumber.capacity))
        .group_by(PoolNumber.country_code)
    ).all()
    pool_util = [
        {"country_code": c, "used": int(u or 0), "capacity": int(cap or 0)}
        for c, u, cap in pool_rows
    ]

    # top resellers
    top_rows = db.execute(
        select(Reseller.id, Reseller.name, Reseller.email, Reseller.plan, Reseller.status,
               func.coalesce(func.sum(case((Order.status == "confirmed", Order.amount), else_=0.0)), 0.0).label("revenue"),
               func.count(Order.id).label("orders"))
        .join(Order, Order.reseller_id == Reseller.id, isouter=True)
        .group_by(Reseller.id)
        .order_by(func.coalesce(func.sum(case((Order.status == "confirmed", Order.amount), else_=0.0)), 0.0).desc())
        .limit(8)
    ).all()
    top_resellers = [
        {
            "id": r[0], "name": r[1], "email": r[2], "plan": r[3], "status": r[4],
            "revenue": float(r[5] or 0.0), "orders": int(r[6] or 0),
        }
        for r in top_rows
    ]

    return AdminStatsOut(
        total_resellers=_delta(total_resellers_curr, total_resellers_prev),
        active_whatsapp=_delta(active_wa, active_wa),  # no historical for connect state
        active_shopify=_delta(active_shop, active_shop),
        total_conversations=_delta(convo_curr, convo_prev),
        total_orders=_delta(orders_curr, orders_prev),
        platform_revenue=_delta(revenue_curr, revenue_prev),
        ai_success_rate=_delta(ai_rate_curr, ai_rate_prev),
        pool_utilization=pool_util,
        top_resellers=top_resellers,
    )
