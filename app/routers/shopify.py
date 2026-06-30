from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_reseller
from ..models import Reseller, ShopifyStore
from ..security import encrypt
from ..services.shopify_client import _normalize_domain, verify_token
from ..services.shopify_sync import sync_store, sync_orders

router = APIRouter(prefix="/me/shopify", tags=["shopify"])


class StoreIn(BaseModel):
    name: str
    shop_domain: str          # accepts 'aurora-store' or full domain
    access_token: str         # shpat_...


class StoreOut(BaseModel):
    id: str
    name: str
    shop_domain: str
    api_version: str
    verified: bool
    last_sync_at: Optional[str] = None
    last_orders_sync_at: Optional[str] = None
    products_synced: int
    orders_synced: int


def _serialize(s: ShopifyStore) -> StoreOut:
    return StoreOut(
        id=s.id, name=s.name, shop_domain=s.shop_domain,
        api_version=s.api_version, verified=s.verified,
        last_sync_at=s.last_sync_at.isoformat() if s.last_sync_at else None,
        last_orders_sync_at=s.last_orders_sync_at.isoformat() if s.last_orders_sync_at else None,
        products_synced=s.products_synced or 0,
        orders_synced=s.orders_synced or 0,
    )


@router.get("/stores", response_model=List[StoreOut])
def list_stores(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(ShopifyStore).where(ShopifyStore.reseller_id == current.id).order_by(ShopifyStore.created_at.desc())
    ).scalars().all()
    return [_serialize(s) for s in rows]


@router.post("/stores", response_model=StoreOut, status_code=status.HTTP_201_CREATED)
async def connect_store(
    payload: StoreIn,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    domain = _normalize_domain(payload.shop_domain)
    if not domain or "myshopify.com" not in domain:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "shop_domain must be like 'mystore.myshopify.com' or 'mystore'")
    if not payload.access_token.startswith("shpat_") and not payload.access_token.startswith("shppa_"):
        # shpat_ for custom apps, shppa_ for legacy private apps
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "access_token doesn't look like a Shopify Admin API token (shpat_…)")

    existing = db.execute(
        select(ShopifyStore).where(ShopifyStore.shop_domain == domain)
    ).scalar_one_or_none()
    if existing and existing.reseller_id != current.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "This Shopify domain is connected to another account")

    # Test the token against Shopify
    check = await verify_token(domain, payload.access_token)
    if not check.get("ok"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Shopify rejected the token (HTTP {check.get('status')}). Double-check the token + domain.",
        )

    if existing:
        existing.name = payload.name or check.get("shop_name") or existing.name
        existing.access_token_enc = encrypt(payload.access_token)
        existing.verified = True
        store = existing
    else:
        store = ShopifyStore(
            reseller_id=current.id,
            name=payload.name or check.get("shop_name") or domain,
            shop_domain=domain,
            access_token_enc=encrypt(payload.access_token),
            verified=True,
        )
        db.add(store)
    db.commit()
    db.refresh(store)
    return _serialize(store)


@router.delete("/stores/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_store(
    store_id: str,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    s = db.get(ShopifyStore, store_id)
    if not s or s.reseller_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "store not found")
    db.delete(s)
    db.commit()


@router.post("/stores/{store_id}/sync")
async def sync(
    store_id: str,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    s = db.get(ShopifyStore, store_id)
    if not s or s.reseller_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "store not found")
    try:
        result = await sync_store(db, current, s)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    return {"ok": True, **result, "last_sync_at": s.last_sync_at.isoformat() if s.last_sync_at else None}


@router.post("/stores/{store_id}/sync-orders")
async def sync_orders_route(
    store_id: str,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
    since: Optional[str] = Query(
        None,
        description="ISO 8601 datetime. Default: store.last_orders_sync_at, or 90d ago on first sync.",
    ),
):
    s = db.get(ShopifyStore, store_id)
    if not s or s.reseller_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "store not found")
    since_dt: Optional[datetime] = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "`since` must be ISO 8601 (e.g. 2026-01-15T00:00:00Z)",
            )
    try:
        result = await sync_orders(db, current, s, since=since_dt)
    except RuntimeError as e:
        # Partial commits survive; last_orders_sync_at intentionally not
        # advanced (see sync_orders docstring). Caller can safely retry.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    return {
        "ok": True,
        **result,
        "last_orders_sync_at": s.last_orders_sync_at.isoformat() if s.last_orders_sync_at else None,
    }
