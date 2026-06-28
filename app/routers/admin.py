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


# ---------- platform settings (singleton) ----------

from pydantic import BaseModel
from ..models import PlatformSettings


def _get_or_create_settings(db: Session) -> PlatformSettings:
    s = db.execute(select(PlatformSettings).limit(1)).scalar_one_or_none()
    if not s:
        s = PlatformSettings()
        db.add(s)
        db.commit()
        db.refresh(s)
    return s


class PlatformSettingsIn(BaseModel):
    platform_name: Optional[str] = None
    support_email: Optional[str] = None
    support_phone: Optional[str] = None
    default_ai_name: Optional[str] = None
    default_ai_tone: Optional[str] = None
    default_response_length: Optional[str] = None
    default_opening_message: Optional[str] = None
    starter_chats_cap: Optional[int] = None
    growth_chats_cap: Optional[int] = None
    scale_chats_cap: Optional[int] = None
    pool_capacity_per_number: Optional[int] = None
    auto_escalate_after_msgs: Optional[int] = None
    ai_typing_delay_ms: Optional[int] = None
    wa_setup_video_url: Optional[str] = None
    shopify_setup_video_url: Optional[str] = None
    ai_training_video_url: Optional[str] = None


class PlatformSettingsOut(BaseModel):
    platform_name: str
    support_email: Optional[str]
    support_phone: Optional[str]
    default_ai_name: str
    default_ai_tone: str
    default_response_length: str
    default_opening_message: Optional[str]
    starter_chats_cap: int
    growth_chats_cap: int
    scale_chats_cap: Optional[int]
    pool_capacity_per_number: int
    auto_escalate_after_msgs: int
    ai_typing_delay_ms: int
    wa_setup_video_url: Optional[str]
    shopify_setup_video_url: Optional[str]
    ai_training_video_url: Optional[str]


def _serialize_settings(s: PlatformSettings) -> PlatformSettingsOut:
    return PlatformSettingsOut(
        platform_name=s.platform_name,
        support_email=s.support_email,
        support_phone=s.support_phone,
        default_ai_name=s.default_ai_name,
        default_ai_tone=s.default_ai_tone,
        default_response_length=s.default_response_length,
        default_opening_message=s.default_opening_message,
        starter_chats_cap=s.starter_chats_cap,
        growth_chats_cap=s.growth_chats_cap,
        scale_chats_cap=s.scale_chats_cap,
        pool_capacity_per_number=s.pool_capacity_per_number,
        auto_escalate_after_msgs=s.auto_escalate_after_msgs,
        ai_typing_delay_ms=s.ai_typing_delay_ms,
        wa_setup_video_url=s.wa_setup_video_url,
        shopify_setup_video_url=s.shopify_setup_video_url,
        ai_training_video_url=s.ai_training_video_url,
    )


@router.get("/platform-settings", response_model=PlatformSettingsOut)
def get_platform_settings(
    _: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _serialize_settings(_get_or_create_settings(db))


@router.put("/platform-settings", response_model=PlatformSettingsOut)
def update_platform_settings(
    payload: PlatformSettingsIn,
    _: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    s = _get_or_create_settings(db)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return _serialize_settings(s)


# ---------- demo / test-data cleanup ----------


class CleanupReport(BaseModel):
    pool_numbers_deleted: int
    pool_counters_reconciled: int
    test_resellers_deleted: int
    demo_reseller_deleted: bool
    orphan_notifications_deleted: int


@router.post("/cleanup-demo-data", response_model=CleanupReport)
def cleanup_demo_data(
    _: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Strip out leftover seed + automated-test artifacts:
      1. Pool numbers without a real Meta WABA token (seed placeholders).
      2. Reconcile PoolNumber.assigned with actual PoolAssignment counts.
      3. Resellers whose emails match test patterns (flow+*, smoke+*,
         test+*, prod-smoke+*, e2e+*).
      4. The seeded demo reseller (demo@arabia-ai.com).
      5. Orphan notifications that point at a reseller no longer present.

    Hard-deletes via services.cleanup.hard_delete_reseller (FK-safe).
    Idempotent — re-running on a clean DB returns all zeros.
    """
    from ..models import PoolNumber, PoolAssignment, Notification
    from ..services.cleanup import hard_delete_reseller

    # 1. Drop pool numbers that were never configured with real WABA creds.
    placeholders = db.execute(
        select(PoolNumber).where(PoolNumber.access_token_enc.is_(None))
    ).scalars().all()
    pool_deleted = 0
    for n in placeholders:
        # Also delete any stale assignments that pointed at this number
        db.execute(
            PoolAssignment.__table__.delete().where(PoolAssignment.pool_number_id == n.id)
        )
        db.delete(n)
        pool_deleted += 1
    db.flush()

    # 2. Reconcile assigned counter on remaining pool numbers from actual
    #    PoolAssignment rows. Past tests left ghost counters.
    remaining = db.execute(select(PoolNumber)).scalars().all()
    reconciled = 0
    for n in remaining:
        real = db.execute(
            select(func.count(PoolAssignment.id))
            .where(PoolAssignment.pool_number_id == n.id)
        ).scalar_one()
        if n.assigned != real:
            n.assigned = real
            n.status = "full" if real >= n.capacity else "active"
            reconciled += 1

    # 3. Test resellers — pattern match
    from sqlalchemy import or_
    test_emails = db.execute(
        select(Reseller).where(or_(
            Reseller.email.like("flow+%"),
            Reseller.email.like("smoke+%"),
            Reseller.email.like("test+%"),
            Reseller.email.like("prod-smoke+%"),
            Reseller.email.like("e2e+%"),
            Reseller.email.like("%@example.com"),
            Reseller.email.like("%@example.test"),
        ))
    ).scalars().all()
    test_deleted = 0
    for r in test_emails:
        hard_delete_reseller(db, r.id)
        test_deleted += 1

    # 4. Seeded demo reseller — the user wants the dashboard to reflect
    #    real state, not demo
    demo = db.execute(
        select(Reseller).where(Reseller.email == "demo@arabia-ai.com")
    ).scalar_one_or_none()
    demo_deleted = False
    if demo:
        hard_delete_reseller(db, demo.id)
        demo_deleted = True

    # 5. Orphan notifications (their reseller is now gone — CASCADE handles
    #    this but if any rows linger, drop them)
    valid_ids = {r[0] for r in db.execute(select(Reseller.id)).all()}
    orphan_notifs = 0
    if valid_ids:
        bad = db.execute(
            select(Notification).where(~Notification.reseller_id.in_(valid_ids))
        ).scalars().all()
        for n in bad:
            db.delete(n)
            orphan_notifs += 1

    db.commit()
    return CleanupReport(
        pool_numbers_deleted=pool_deleted,
        pool_counters_reconciled=reconciled,
        test_resellers_deleted=test_deleted,
        demo_reseller_deleted=demo_deleted,
        orphan_notifications_deleted=orphan_notifs,
    )
