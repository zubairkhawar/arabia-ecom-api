from typing import List, Optional
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from ..db import get_db
from ..deps import get_current_reseller, require_admin
from ..models import Reseller, Plan, Usage, Order

router = APIRouter(prefix="/billing", tags=["billing"])


class PlanOut(BaseModel):
    code: str
    name: str
    price: float
    currency: str
    orders_cap: Optional[int]
    conversations_cap: Optional[int]
    stores_cap: Optional[int]
    universal_numbers_cap: Optional[int]
    features: List[str] = []


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


def _ensure_current_cycle(db: Session, reseller: Reseller) -> Usage:
    today = date.today()
    u = db.execute(
        select(Usage).where(Usage.reseller_id == reseller.id,
                            Usage.cycle_start <= today, Usage.cycle_end >= today)
    ).scalar_one_or_none()
    if u:
        return u
    # monthly cycle starting on signup-day-of-month, simplified to calendar month
    from calendar import monthrange
    start = today.replace(day=1)
    end = start.replace(day=monthrange(today.year, today.month)[1])
    # Best-effort live counter from orders table
    orders_used = db.execute(
        select(func.count(Order.id)).where(
            Order.reseller_id == reseller.id, Order.created_at >= start
        )
    ).scalar_one()
    u = Usage(
        reseller_id=reseller.id,
        cycle_start=start,
        cycle_end=end,
        orders_used=orders_used,
        conversations_used=0,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@router.get("/overview", response_model=BillingOverview)
def overview(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    plan = db.execute(select(Plan).where(Plan.code == current.plan)).scalar_one_or_none()
    if not plan:
        plan = Plan(code=current.plan, name=current.plan.title(), price=0)
    usage = _ensure_current_cycle(db, current)
    plans = db.execute(select(Plan).order_by(Plan.price)).scalars().all()
    return BillingOverview(
        plan=PlanOut(
            code=plan.code, name=plan.name, price=plan.price, currency=plan.currency,
            orders_cap=plan.orders_cap, conversations_cap=plan.conversations_cap,
            stores_cap=plan.stores_cap, universal_numbers_cap=plan.universal_numbers_cap,
            features=plan.features or [],
        ),
        usage=UsageOut(
            cycle_start=usage.cycle_start, cycle_end=usage.cycle_end,
            orders_used=usage.orders_used, orders_cap=plan.orders_cap,
            conversations_used=usage.conversations_used, conversations_cap=plan.conversations_cap,
        ),
        plans=[
            PlanOut(
                code=p.code, name=p.name, price=p.price, currency=p.currency,
                orders_cap=p.orders_cap, conversations_cap=p.conversations_cap,
                stores_cap=p.stores_cap, universal_numbers_cap=p.universal_numbers_cap,
                features=p.features or [],
            )
            for p in plans
        ],
    )


@router.post("/upgrade")
def upgrade(
    plan_code: str,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    """Stub — no payment. Sets the plan field."""
    plan = db.execute(select(Plan).where(Plan.code == plan_code)).scalar_one_or_none()
    if not plan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "plan not found")
    current.plan = plan.code
    db.commit()
    return {"ok": True, "plan": plan.code}


# Admin: seed/maintain plan definitions
class PlanIn(BaseModel):
    code: str
    name: str
    price: float
    currency: str = "AED"
    orders_cap: Optional[int] = None
    conversations_cap: Optional[int] = None
    stores_cap: Optional[int] = None
    universal_numbers_cap: Optional[int] = None
    features: List[str] = []


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
    return PlanOut(
        code=p.code, name=p.name, price=p.price, currency=p.currency,
        orders_cap=p.orders_cap, conversations_cap=p.conversations_cap,
        stores_cap=p.stores_cap, universal_numbers_cap=p.universal_numbers_cap,
        features=p.features or [],
    )
