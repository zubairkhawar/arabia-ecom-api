from typing import List, Optional
from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select, func, desc

from ..config import settings
from ..db import get_db
from ..deps import get_current_reseller, require_admin
from ..models import (
    Reseller, Plan, Usage, Order, Subscription, CreditLedger, Payment,
)
from ..services import credits as credits_service
from ..services import billing as billing_service
from ..services import payments_tap

router = APIRouter(prefix="/billing", tags=["billing"])


# ---------- Schemas ----------

class PlanOut(BaseModel):
    code: str
    name: str
    price: float
    price_annual: float
    currency: str
    monthly_credits: int
    orders_cap: Optional[int]
    conversations_cap: Optional[int]
    stores_cap: Optional[int]
    universal_numbers_cap: Optional[int]
    features: List[str] = []
    sort_order: int = 0


class SubscriptionOut(BaseModel):
    plan: PlanOut
    status: str
    billing_cycle: str
    trial_ends_at: Optional[datetime] = None
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    credits_balance: int
    credits_granted_this_period: int
    credits_used_this_period: int
    is_trial: bool
    is_paused: bool
    days_left_in_trial: Optional[int] = None
    available_plans: List[PlanOut]


class LedgerRow(BaseModel):
    delta: int
    reason: str
    note: Optional[str]
    balance_after: int
    occurred_at: datetime


class UsageOut(BaseModel):
    cycle_start: date
    cycle_end: date
    orders_used: int
    orders_cap: Optional[int]
    conversations_used: int
    conversations_cap: Optional[int]


class BillingOverview(BaseModel):
    plan: PlanOut
    usage: UsageOut
    plans: List[PlanOut]


# ---------- Helpers ----------

def _plan_out(p: Plan) -> PlanOut:
    return PlanOut(
        code=p.code, name=p.name, price=p.price,
        price_annual=p.price_annual or 0.0, currency=p.currency,
        monthly_credits=p.monthly_credits or 0,
        orders_cap=p.orders_cap, conversations_cap=p.conversations_cap,
        stores_cap=p.stores_cap, universal_numbers_cap=p.universal_numbers_cap,
        features=p.features or [], sort_order=p.sort_order or 0,
    )


def _virtual_trial_plan(monthly_credits: int = 50) -> PlanOut:
    return PlanOut(
        code="trial", name="Free trial", price=0.0, price_annual=0.0,
        currency="AED", monthly_credits=monthly_credits,
        orders_cap=None, conversations_cap=None, stores_cap=None,
        universal_numbers_cap=None,
        features=["7-day evaluation", "50 AI conversations", "All features unlocked"],
        sort_order=0,
    )


def _ensure_current_cycle(db: Session, reseller: Reseller) -> Usage:
    """Legacy monthly-orders counter — kept for /billing/overview."""
    today = date.today()
    u = db.execute(
        select(Usage).where(Usage.reseller_id == reseller.id,
                            Usage.cycle_start <= today, Usage.cycle_end >= today)
    ).scalar_one_or_none()
    if u:
        return u
    from calendar import monthrange
    start = today.replace(day=1)
    end = start.replace(day=monthrange(today.year, today.month)[1])
    orders_used = db.execute(
        select(func.count(Order.id)).where(
            Order.reseller_id == reseller.id, Order.created_at >= start
        )
    ).scalar_one()
    u = Usage(
        reseller_id=reseller.id,
        cycle_start=start, cycle_end=end,
        orders_used=orders_used, conversations_used=0,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ---------- Reseller endpoints ----------

@router.get("/me/subscription", response_model=SubscriptionOut)
def my_subscription(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    sub = credits_service.get_or_init_subscription(db, current.id)
    if not sub:
        sub = billing_service.start_trial(db, current.id)
        db.commit()

    plan = db.execute(select(Plan).where(Plan.code == sub.plan_code)).scalar_one_or_none()
    plan_out = _plan_out(plan) if plan else _virtual_trial_plan(billing_service.TRIAL_CREDITS)
    available = [
        _plan_out(p) for p in db.execute(
            select(Plan).where(Plan.is_public == True).order_by(Plan.sort_order, Plan.price)
        ).scalars().all()
    ]

    days_left = None
    if sub.status == "trial" and sub.trial_ends_at:
        from datetime import timezone
        now = datetime.now(timezone.utc)
        end = sub.trial_ends_at
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        days_left = max(0, (end - now).days)

    return SubscriptionOut(
        plan=plan_out,
        status=sub.status,
        billing_cycle=sub.billing_cycle,
        trial_ends_at=sub.trial_ends_at,
        current_period_start=sub.current_period_start,
        current_period_end=sub.current_period_end,
        cancelled_at=sub.cancelled_at,
        credits_balance=sub.credits_balance or 0,
        credits_granted_this_period=sub.credits_granted_this_period or 0,
        credits_used_this_period=sub.credits_used_this_period or 0,
        is_trial=(sub.status == "trial"),
        is_paused=(sub.status in ("paused", "cancelled", "past_due")),
        days_left_in_trial=days_left,
        available_plans=available,
    )


class UpgradeIn(BaseModel):
    plan_code: str
    billing_cycle: str = "monthly"  # monthly|annual


@router.post("/me/subscription/activate", response_model=SubscriptionOut)
def activate_subscription(
    payload: UpgradeIn,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    """Activate a paid plan immediately — used when payment is already
    captured (Tap webhook hits this internally) or for admin overrides.
    For a real customer-initiated upgrade flow, the portal should call
    POST /me/subscription/checkout instead."""
    if payload.billing_cycle not in ("monthly", "annual"):
        raise HTTPException(422, "billing_cycle must be 'monthly' or 'annual'")
    try:
        billing_service.activate_paid(
            db, current.id, plan_code=payload.plan_code,
            billing_cycle=payload.billing_cycle,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    db.commit()
    return my_subscription(current, db)


@router.post("/me/subscription/cancel", response_model=SubscriptionOut)
def cancel_subscription(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    """Stops auto-renewal. Subscription stays active until period end."""
    billing_service.cancel(db, current.id)
    db.commit()
    return my_subscription(current, db)


class CheckoutIn(BaseModel):
    plan_code: str
    billing_cycle: str = "monthly"  # monthly|annual


class CheckoutOut(BaseModel):
    charge_id: str
    redirect_url: str
    amount: float
    currency: str
    plan_code: str
    billing_cycle: str


@router.post("/me/subscription/checkout", response_model=CheckoutOut)
async def start_checkout(
    payload: CheckoutIn,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    """Create a Tap Charge for the chosen plan. Returns the redirect URL
    for the hosted Tap checkout page. After payment, Tap calls
    POST /webhooks/tap → we activate the subscription."""
    if payload.billing_cycle not in ("monthly", "annual"):
        raise HTTPException(422, "billing_cycle must be 'monthly' or 'annual'")
    plan = db.execute(select(Plan).where(Plan.code == payload.plan_code)).scalar_one_or_none()
    if not plan:
        raise HTTPException(404, "plan not found")

    amount = plan.price_annual if payload.billing_cycle == "annual" else plan.price
    if amount <= 0:
        raise HTTPException(422, "plan price is zero — can't checkout")

    description = f"{plan.name} ({payload.billing_cycle}) — Arabia AI"
    redirect_url = f"{settings.frontend_base_url}/reseller/billing?status=success"
    webhook_url = f"{settings.app_base_url}/webhooks/tap"

    try:
        charge = await payments_tap.create_charge(
            reseller_id=current.id,
            amount=amount,
            currency=plan.currency or "AED",
            description=description,
            customer_name=current.name,
            customer_email=current.email,
            redirect_url=redirect_url,
            webhook_url=webhook_url,
            metadata={
                "reseller_id": current.id,
                "plan_code": plan.code,
                "billing_cycle": payload.billing_cycle,
            },
        )
    except Exception as e:
        raise HTTPException(502, f"Tap charge create failed: {e}")

    # Persist the initiated payment so we can reconcile via webhook
    db.add(Payment(
        reseller_id=current.id,
        kind="subscription",
        amount=amount,
        currency=plan.currency or "AED",
        status="initiated",
        tap_charge_id=charge["id"],
        plan_code=plan.code,
        credits_granted=None,
        meta={"billing_cycle": payload.billing_cycle},
    ))
    db.commit()

    transaction = charge.get("transaction") or {}
    redirect = transaction.get("url") or redirect_url
    return CheckoutOut(
        charge_id=charge["id"],
        redirect_url=redirect,
        amount=amount,
        currency=plan.currency or "AED",
        plan_code=plan.code,
        billing_cycle=payload.billing_cycle,
    )


class TopupIn(BaseModel):
    credits: int  # 100 | 500 | 2000


TOPUP_PRICES_AED = {100: 79.0, 500: 299.0, 2000: 999.0}


@router.post("/me/credits/topup", response_model=CheckoutOut)
async def topup_credits(
    payload: TopupIn,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    """Create a one-off Tap Charge for a credit top-up bundle."""
    if payload.credits not in TOPUP_PRICES_AED:
        raise HTTPException(422, f"credits must be one of {list(TOPUP_PRICES_AED.keys())}")
    amount = TOPUP_PRICES_AED[payload.credits]
    description = f"Top-up: {payload.credits} credits — Arabia AI"
    redirect_url = f"{settings.frontend_base_url}/reseller/billing?status=topup_success"
    webhook_url = f"{settings.app_base_url}/webhooks/tap"

    try:
        charge = await payments_tap.create_charge(
            reseller_id=current.id,
            amount=amount,
            currency="AED",
            description=description,
            customer_name=current.name,
            customer_email=current.email,
            redirect_url=redirect_url,
            webhook_url=webhook_url,
            metadata={
                "reseller_id": current.id,
                "topup_credits": payload.credits,
            },
        )
    except Exception as e:
        raise HTTPException(502, f"Tap charge create failed: {e}")

    db.add(Payment(
        reseller_id=current.id,
        kind="topup",
        amount=amount,
        currency="AED",
        status="initiated",
        tap_charge_id=charge["id"],
        plan_code=None,
        credits_granted=payload.credits,
        meta={"credits": payload.credits},
    ))
    db.commit()
    transaction = charge.get("transaction") or {}
    redirect = transaction.get("url") or redirect_url
    return CheckoutOut(
        charge_id=charge["id"],
        redirect_url=redirect,
        amount=amount,
        currency="AED",
        plan_code="topup",
        billing_cycle="one-time",
    )


@router.get("/me/credits/ledger", response_model=List[LedgerRow])
def my_credit_ledger(
    limit: int = 50,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(CreditLedger)
        .where(CreditLedger.reseller_id == current.id)
        .order_by(desc(CreditLedger.created_at))
        .limit(min(limit, 200))
    ).scalars().all()
    return [
        LedgerRow(
            delta=r.delta, reason=r.reason, note=r.note,
            balance_after=r.balance_after, occurred_at=r.created_at,
        )
        for r in rows
    ]


# ---------- Public pricing (no auth) ----------

@router.get("/plans", response_model=List[PlanOut])
def list_public_plans(db: Session = Depends(get_db)):
    """Used by the landing page pricing section. Cached client-side."""
    rows = db.execute(
        select(Plan).where(Plan.is_public == True).order_by(Plan.sort_order, Plan.price)
    ).scalars().all()
    return [_plan_out(p) for p in rows]


# ---------- Legacy overview (kept for back-compat) ----------

@router.get("/overview", response_model=BillingOverview)
def overview(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    plan = db.execute(select(Plan).where(Plan.code == current.plan)).scalar_one_or_none()
    if not plan:
        # Fall back to a virtual placeholder if their plan code isn't
        # in the plans table (e.g. old "silver" code).
        plan = Plan(code=current.plan, name=current.plan.title(), price=0)
    usage = _ensure_current_cycle(db, current)
    plans = db.execute(select(Plan).order_by(Plan.price)).scalars().all()
    return BillingOverview(
        plan=_plan_out(plan),
        usage=UsageOut(
            cycle_start=usage.cycle_start, cycle_end=usage.cycle_end,
            orders_used=usage.orders_used, orders_cap=plan.orders_cap,
            conversations_used=usage.conversations_used, conversations_cap=plan.conversations_cap,
        ),
        plans=[_plan_out(p) for p in plans],
    )


@router.post("/upgrade")
def upgrade_legacy(
    plan_code: str,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    """Deprecated — kept so older clients don't 404. New clients should
    use POST /me/subscription/activate (after payment)."""
    plan = db.execute(select(Plan).where(Plan.code == plan_code)).scalar_one_or_none()
    if not plan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "plan not found")
    current.plan = plan.code
    db.commit()
    return {"ok": True, "plan": plan.code}


# ---------- Admin endpoints ----------

class PlanIn(BaseModel):
    code: str
    name: str
    price: float
    price_annual: float = 0.0
    currency: str = "AED"
    monthly_credits: int = 0
    orders_cap: Optional[int] = None
    conversations_cap: Optional[int] = None
    stores_cap: Optional[int] = None
    universal_numbers_cap: Optional[int] = None
    features: List[str] = []
    is_public: bool = True
    sort_order: int = 0


@router.put("/plans/{code}", response_model=PlanOut)
def upsert_plan(
    code: str,
    payload: PlanIn,
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
):
    p = db.execute(select(Plan).where(Plan.code == code)).scalar_one_or_none()
    if not p:
        p = Plan(code=code)
        db.add(p)
    for k, v in payload.model_dump(exclude={"code"}).items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return _plan_out(p)


class GrantCreditsIn(BaseModel):
    reseller_id: str
    amount: int
    note: Optional[str] = None


@router.post("/admin/credits/grant")
def admin_grant_credits(
    payload: GrantCreditsIn,
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Manually credit a reseller — support / goodwill grants."""
    if payload.amount <= 0:
        raise HTTPException(422, "amount must be positive")
    try:
        balance = credits_service.grant(
            db, payload.reseller_id, amount=payload.amount,
            reason="admin_grant", note=payload.note or "manual admin grant",
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    db.commit()
    return {"ok": True, "new_balance": balance}


class AdminSubRow(BaseModel):
    reseller_id: str
    reseller_name: str
    reseller_email: str
    plan_code: str
    status: str
    credits_balance: int
    credits_used_this_period: int
    trial_ends_at: Optional[datetime]
    current_period_end: Optional[datetime]
    created_at: datetime


@router.get("/admin/subscriptions", response_model=List[AdminSubRow])
def admin_list_subscriptions(
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(Subscription, Reseller)
        .join(Reseller, Reseller.id == Subscription.reseller_id)
        .order_by(desc(Subscription.created_at))
    ).all()
    return [
        AdminSubRow(
            reseller_id=r.id, reseller_name=r.name, reseller_email=r.email,
            plan_code=s.plan_code, status=s.status,
            credits_balance=s.credits_balance or 0,
            credits_used_this_period=s.credits_used_this_period or 0,
            trial_ends_at=s.trial_ends_at,
            current_period_end=s.current_period_end,
            created_at=s.created_at,
        )
        for (s, r) in rows
    ]
