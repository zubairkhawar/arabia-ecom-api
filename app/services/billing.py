"""Subscription lifecycle: trial, paid activation, period rollover,
trial expiry, grace handling. Pairs with services/credits.py — this
module decides WHEN credits are granted; credits.py decides HOW."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import select

from ..models import Subscription, Plan
from . import credits as credits_service


TRIAL_DAYS = 7
TRIAL_CREDITS = 50

# Hardcoded fallback plans — used if the DB Plan rows are missing
# (typical only on a fresh install before /admin/billing/seed runs).
DEFAULT_PLANS = [
    {
        "code": "starter", "name": "Starter", "price": 99.0, "price_annual": 990.0,
        "currency": "AED", "monthly_credits": 200, "sort_order": 10,
        "features": [
            "200 AI conversations / month",
            "Universal pool number included",
            "Meta Pixel + CAPI attribution",
            "Single Shopify store",
            "Email support",
        ],
    },
    {
        "code": "growth", "name": "Growth", "price": 249.0, "price_annual": 2490.0,
        "currency": "AED", "monthly_credits": 750, "sort_order": 20,
        "features": [
            "750 AI conversations / month",
            "Own WhatsApp number supported",
            "Up to 3 Shopify stores",
            "Trilingual AI (EN / AR / Roman Urdu)",
            "Priority email support",
        ],
    },
    {
        "code": "scale", "name": "Scale", "price": 599.0, "price_annual": 5990.0,
        "currency": "AED", "monthly_credits": 2500, "sort_order": 30,
        "features": [
            "2,500 AI conversations / month",
            "Unlimited Shopify stores",
            "Advanced attribution dashboard",
            "Real-agent escalation",
            "WhatsApp + email support",
        ],
    },
]


def ensure_plans(db: Session) -> None:
    """Idempotently seed the canonical plan rows. Safe to call on every
    startup — only inserts missing codes."""
    existing = {
        p.code for p in db.execute(select(Plan)).scalars().all()
    }
    for p in DEFAULT_PLANS:
        if p["code"] in existing:
            continue
        db.add(Plan(
            code=p["code"], name=p["name"],
            price=p["price"], price_annual=p["price_annual"],
            currency=p["currency"], monthly_credits=p["monthly_credits"],
            features=p["features"], sort_order=p["sort_order"],
            is_public=True,
        ))
    db.commit()


def start_trial(db: Session, reseller_id: str) -> Subscription:
    """Create the reseller's initial Subscription row in trial mode.
    Idempotent — returns the existing row if one already exists."""
    existing = db.execute(
        select(Subscription).where(Subscription.reseller_id == reseller_id)
    ).scalar_one_or_none()
    if existing:
        return existing

    now = datetime.now(timezone.utc)
    sub = Subscription(
        reseller_id=reseller_id,
        plan_code="trial",
        status="trial",
        billing_cycle="monthly",
        trial_ends_at=now + timedelta(days=TRIAL_DAYS),
        current_period_start=now,
        current_period_end=now + timedelta(days=TRIAL_DAYS),
        credits_balance=0,  # set by credits.grant below
        credits_granted_this_period=0,
        credits_used_this_period=0,
    )
    db.add(sub)
    db.flush()
    credits_service.grant(
        db, reseller_id, amount=TRIAL_CREDITS,
        reason="trial_seed",
        note=f"7-day trial — {TRIAL_CREDITS} credits to evaluate",
    )
    db.flush()
    return sub


def activate_paid(
    db: Session,
    reseller_id: str,
    plan_code: str,
    billing_cycle: str = "monthly",
) -> Subscription:
    """Flip a trial / paused / cancelled subscription to active on the
    given plan. Resets the credit balance to the plan's monthly grant
    and starts a fresh billing period."""
    plan = db.execute(select(Plan).where(Plan.code == plan_code)).scalar_one_or_none()
    if not plan:
        raise ValueError(f"unknown plan: {plan_code}")
    if billing_cycle not in ("monthly", "annual"):
        raise ValueError("billing_cycle must be 'monthly' or 'annual'")

    sub = credits_service.get_or_init_subscription(db, reseller_id)
    if not sub:
        sub = Subscription(reseller_id=reseller_id)
        db.add(sub)
        db.flush()

    now = datetime.now(timezone.utc)
    sub.plan_code = plan.code
    sub.status = "active"
    sub.billing_cycle = billing_cycle
    sub.current_period_start = now
    sub.current_period_end = now + (timedelta(days=365) if billing_cycle == "annual" else timedelta(days=30))
    sub.cancelled_at = None
    sub.trial_ends_at = None  # trial concluded

    # Annual cycle still grants `monthly_credits` per month — roll the
    # period 12 times and grant fresh credits at each tick (handled by
    # cron). For now, grant the first month's bucket.
    credits_service.reset_period(
        db, reseller_id, plan_credits=plan.monthly_credits,
        reason="plan_activated",
    )
    db.flush()
    return sub


def cancel(db: Session, reseller_id: str) -> Subscription:
    """User-initiated cancellation. Subscription stays active until
    current_period_end, then cron flips it to cancelled."""
    sub = credits_service.get_or_init_subscription(db, reseller_id)
    if not sub:
        raise ValueError("no subscription")
    sub.cancelled_at = datetime.now(timezone.utc)
    db.flush()
    return sub


def expire_trials(db: Session) -> int:
    """Cron job — flip trials whose trial_ends_at is past to 'paused'.
    Returns count flipped."""
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(Subscription).where(
            Subscription.status == "trial",
            Subscription.trial_ends_at.is_not(None),
            Subscription.trial_ends_at < now,
        )
    ).scalars().all()
    for sub in rows:
        sub.status = "paused"
    db.flush()
    return len(rows)


def roll_periods(db: Session) -> int:
    """Cron job — for active subscriptions whose current_period_end is
    past, refill credits and start a new period (monthly tick). For
    annual cycles, still refills monthly (annual = pay yearly, get a
    fresh bucket monthly). Cancelled subscriptions whose period ends
    are flipped to cancelled.

    Returns count rolled."""
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(Subscription).where(
            Subscription.status == "active",
            Subscription.current_period_end.is_not(None),
            Subscription.current_period_end < now,
        )
    ).scalars().all()
    n = 0
    for sub in rows:
        if sub.cancelled_at:
            sub.status = "cancelled"
            continue
        plan = db.execute(select(Plan).where(Plan.code == sub.plan_code)).scalar_one_or_none()
        if not plan:
            sub.status = "past_due"
            continue
        sub.current_period_start = now
        sub.current_period_end = now + timedelta(days=30)  # monthly tick regardless of cycle
        credits_service.reset_period(db, sub.reseller_id, plan_credits=plan.monthly_credits)
        n += 1
    db.flush()
    return n
