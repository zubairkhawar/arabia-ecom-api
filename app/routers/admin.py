from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from ..db import get_db
from ..deps import require_admin
from ..models import (
    Reseller, AdminUser, PoolNumber, PoolAssignment, Order, ClickSession,
)
from ..security import encrypt, hash_password
from ..schemas.admin import (
    PoolNumberIn, PoolNumberUpdate, PoolNumberOut,
    PoolAssignmentOut, AdminUserIn, AdminUserToggle, AdminUserOut,
    ResellerSummary,
)

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------- resellers ----------

@router.get("/resellers", response_model=List[ResellerSummary])
def list_resellers(
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = db.execute(select(Reseller).order_by(Reseller.created_at.desc())).scalars().all()
    return [ResellerSummary.model_validate(r) for r in rows]


@router.get("/resellers/{reseller_id}/summary")
def reseller_summary(
    reseller_id: str,
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
):
    r = db.get(Reseller, reseller_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reseller not found")
    orders_count = db.execute(
        select(func.count(Order.id)).where(Order.reseller_id == reseller_id)
    ).scalar_one()
    revenue = db.execute(
        select(func.coalesce(func.sum(Order.amount), 0.0))
        .where(Order.reseller_id == reseller_id, Order.status == "confirmed")
    ).scalar_one()
    clicks = db.execute(
        select(func.count(ClickSession.id)).where(ClickSession.reseller_id == reseller_id)
    ).scalar_one()
    return {
        "reseller": ResellerSummary.model_validate(r).model_dump(),
        "orders_count": orders_count,
        "revenue": float(revenue or 0.0),
        "clicks": clicks,
    }


@router.post("/resellers/{reseller_id}/suspend", response_model=ResellerSummary)
def suspend(
    reseller_id: str,
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
):
    r = db.get(Reseller, reseller_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reseller not found")
    r.status = "suspended"
    db.commit()
    return ResellerSummary.model_validate(r)


@router.post("/resellers/{reseller_id}/reactivate", response_model=ResellerSummary)
def reactivate(
    reseller_id: str,
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
):
    r = db.get(Reseller, reseller_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reseller not found")
    r.status = "active"
    db.commit()
    return ResellerSummary.model_validate(r)


# ---------- number pool ----------

@router.get("/pool-numbers", response_model=List[PoolNumberOut])
def list_pool_numbers(
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
    country: Optional[str] = None,
):
    stmt = select(PoolNumber).order_by(PoolNumber.country_code, PoolNumber.number)
    if country:
        stmt = stmt.where(PoolNumber.country_code == country)
    rows = db.execute(stmt).scalars().all()
    return [
        PoolNumberOut(
            id=n.id, number=n.number, country=n.country, country_code=n.country_code,
            flag=n.flag, capacity=n.capacity, assigned=n.assigned, status=n.status,
            has_token=bool(n.access_token_enc),
        )
        for n in rows
    ]


@router.post("/pool-numbers", response_model=PoolNumberOut, status_code=status.HTTP_201_CREATED)
def create_pool_number(
    payload: PoolNumberIn,
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
):
    n = PoolNumber(
        number=payload.number,
        country=payload.country,
        country_code=payload.country_code,
        flag=payload.flag,
        capacity=payload.capacity,
        waba_id=payload.waba_id,
        phone_number_id=payload.phone_number_id,
        access_token_enc=encrypt(payload.access_token) if payload.access_token else None,
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return PoolNumberOut(
        id=n.id, number=n.number, country=n.country, country_code=n.country_code,
        flag=n.flag, capacity=n.capacity, assigned=n.assigned, status=n.status,
        has_token=bool(n.access_token_enc),
    )


@router.patch("/pool-numbers/{number_id}", response_model=PoolNumberOut)
def update_pool_number(
    number_id: str,
    payload: PoolNumberUpdate,
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
):
    n = db.get(PoolNumber, number_id)
    if not n:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "pool number not found")
    if payload.status:
        if payload.status not in ("active", "disabled", "full"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "status must be active|disabled|full")
        n.status = payload.status
    if payload.capacity is not None:
        n.capacity = payload.capacity
        if n.assigned >= n.capacity:
            n.status = "full"
    db.commit()
    db.refresh(n)
    return PoolNumberOut(
        id=n.id, number=n.number, country=n.country, country_code=n.country_code,
        flag=n.flag, capacity=n.capacity, assigned=n.assigned, status=n.status,
        has_token=bool(n.access_token_enc),
    )


@router.get("/pool-assignments", response_model=List[PoolAssignmentOut])
def list_assignments(
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = db.execute(select(PoolAssignment)).scalars().all()
    out = []
    for a in rows:
        r = db.get(Reseller, a.reseller_id)
        n = db.get(PoolNumber, a.pool_number_id)
        out.append(PoolAssignmentOut(
            reseller_id=a.reseller_id,
            reseller_name=r.name if r else "?",
            pool_number_id=a.pool_number_id,
            number=n.number if n else "?",
            country_code=n.country_code if n else "?",
        ))
    return out


# ---------- admin users ----------

@router.get("/users", response_model=List[AdminUserOut])
def list_admins(
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return db.execute(select(AdminUser).order_by(AdminUser.created_at.desc())).scalars().all()


@router.post("/users", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
def add_admin(
    payload: AdminUserIn,
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
):
    existing = db.execute(select(AdminUser).where(AdminUser.email == payload.email)).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    a = AdminUser(
        name=payload.name,
        email=payload.email,
        level=payload.level,
        enabled=True,
        password_hash=hash_password("change-me-now"),
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def toggle_admin(
    user_id: str,
    payload: AdminUserToggle,
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
):
    a = db.get(AdminUser, user_id)
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "admin not found")
    a.enabled = payload.enabled
    db.commit()
    db.refresh(a)
    return a


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_admin(
    user_id: str,
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
):
    a = db.get(AdminUser, user_id)
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "admin not found")
    if a.level == "Owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cannot remove Owner")
    db.delete(a)
    db.commit()


# ---------- cross-reseller views ----------

@router.get("/orders", response_model=List[dict])
def admin_orders(
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
    reseller_id: Optional[str] = None,
):
    stmt = select(Order).order_by(Order.created_at.desc()).limit(500)
    if reseller_id:
        stmt = stmt.where(Order.reseller_id == reseller_id)
    rows = db.execute(stmt).scalars().all()
    return [
        {
            "id": o.id, "code": o.code, "reseller_id": o.reseller_id,
            "customer_id": o.customer_id, "amount": o.amount, "currency": o.currency,
            "status": o.status, "delivery_status": o.delivery_status,
            "channel": o.channel, "source_platform": o.source_platform,
            "created_at": o.created_at.isoformat(),
        }
        for o in rows
    ]
