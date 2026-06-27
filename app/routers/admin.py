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
async def create_pool_number(
    payload: PoolNumberIn,
    _: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    # If Meta credentials were provided, verify them BEFORE persisting so
    # the admin gets immediate feedback that the pool number actually works.
    if payload.access_token and payload.phone_number_id:
        from ..services.whatsapp_cloud import verify_creds
        check = await verify_creds(payload.phone_number_id, payload.access_token)
        if not check["ok"]:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Meta rejected the pool credentials (HTTP {check['status']}). "
                f"Meta said: {check['body'][:200]}",
            )

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
    _: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Disabled — the platform has exactly one admin account, configured
    via ADMIN_EMAIL env var. Additional admin users cannot be created."""
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        "Multi-admin is disabled. The sole admin is configured via ADMIN_EMAIL.",
    )


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def toggle_admin(
    user_id: str,
    payload: AdminUserToggle,
    _: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from ..config import settings
    a = db.get(AdminUser, user_id)
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "admin not found")
    if a.email.lower() == settings.admin_email.lower():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot disable the sole platform admin.")
    a.enabled = payload.enabled
    db.commit()
    db.refresh(a)
    return a


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_admin(
    user_id: str,
    _: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from ..config import settings
    a = db.get(AdminUser, user_id)
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "admin not found")
    if a.email.lower() == settings.admin_email.lower() or a.level == "Owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot delete the sole platform admin.")
    db.delete(a)
    db.commit()


# ---------- cross-reseller views ----------

@router.get("/orders", response_model=List[dict])
def admin_orders(
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
    reseller_id: Optional[str] = None,
):
    from ..models import Customer
    stmt = select(Order).order_by(Order.created_at.desc()).limit(500)
    if reseller_id:
        stmt = stmt.where(Order.reseller_id == reseller_id)
    rows = db.execute(stmt).scalars().all()
    out = []
    for o in rows:
        cust = db.get(Customer, o.customer_id)
        reseller = db.get(Reseller, o.reseller_id)
        out.append({
            "id": o.id, "code": o.code,
            "reseller_id": o.reseller_id,
            "reseller_name": reseller.name if reseller else "?",
            "customer_id": o.customer_id,
            "customer_name": cust.name if cust else None,
            "customer_phone": cust.phone if cust else None,
            "amount": o.amount, "currency": o.currency,
            "status": o.status, "delivery_status": o.delivery_status,
            "channel": o.channel, "source_platform": o.source_platform,
            "tracking_number": o.tracking_number,
            "source": o.source,
            "created_at": o.created_at.isoformat(),
        })
    return out


@router.get("/chats", response_model=List[dict])
def admin_chats(
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
    reseller_id: Optional[str] = None,
):
    """Cross-reseller chat list — admin can see whose customer each chat belongs to."""
    from ..models import Chat, Customer
    stmt = select(Chat).order_by(Chat.updated_at.desc()).limit(500)
    if reseller_id:
        stmt = stmt.where(Chat.reseller_id == reseller_id)
    rows = db.execute(stmt).scalars().all()
    out = []
    for c in rows:
        cust = db.get(Customer, c.customer_id)
        reseller = db.get(Reseller, c.reseller_id)
        last = c.messages[-1] if c.messages else None
        out.append({
            "id": c.id,
            "reseller_id": c.reseller_id,
            "reseller_name": reseller.name if reseller else "?",
            "customer_id": c.customer_id,
            "customer_name": cust.name if cust else None,
            "customer_phone": cust.phone if cust else "",
            "channel": c.channel,
            "mode": c.mode,
            "unread": c.unread,
            "last_message": last.text if last else None,
            "last_message_at": last.created_at.isoformat() if last else None,
        })
    return out
