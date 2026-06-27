"""Sync Shopify products → our products table.

Each Shopify product becomes (or updates) one Product row tagged with
shopify_store_id + shopify_product_id. Re-running the sync upserts.
"""
from datetime import datetime, timezone
from typing import Dict, Any
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Product, Reseller, ShopifyStore
from .shopify_client import fetch_products
from .slug import short_slug


def _price_from(p: Dict[str, Any]) -> float:
    variants = p.get("variants") or []
    if variants and variants[0].get("price"):
        try:
            return float(variants[0]["price"])
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _image_from(p: Dict[str, Any]) -> str | None:
    img = p.get("image") or {}
    return img.get("src")


async def sync_store(db: Session, reseller: Reseller, store: ShopifyStore) -> Dict[str, int]:
    """Pull products from Shopify and upsert them as Product rows.
    Returns counts: {fetched, created, updated, skipped}."""
    items = await fetch_products(store.shop_domain, store.access_token_enc, store.api_version)
    created = updated = skipped = 0
    for sp in items:
        sid = str(sp.get("id") or "")
        if not sid:
            skipped += 1
            continue
        existing = db.execute(
            select(Product).where(
                Product.reseller_id == reseller.id,
                Product.shopify_store_id == store.id,
                Product.shopify_product_id == sid,
            )
        ).scalar_one_or_none()
        name = sp.get("title") or "Untitled"
        body = sp.get("body_html") or None
        price = _price_from(sp)
        image = _image_from(sp)
        if existing:
            existing.name = name
            existing.main_description = body
            existing.price = price
            existing.image_url = image or existing.image_url
            existing.active = sp.get("status") == "active"
            updated += 1
        else:
            db.add(Product(
                reseller_id=reseller.id,
                name=name,
                slug=short_slug(),
                image_url=image,
                description=(name[:140] if not body else None),
                main_description=body,
                key_points=[],
                price=price,
                currency=reseller.currency,
                country=reseller.country,
                channels=["whatsapp", "shopify"],
                source=f"shopify:{store.name}",
                shopify_store_id=store.id,
                shopify_product_id=sid,
                active=sp.get("status") == "active",
            ))
            created += 1
    store.products_synced = (store.products_synced or 0) + created
    store.last_sync_at = datetime.now(timezone.utc)
    db.commit()
    return {"fetched": len(items), "created": created, "updated": updated, "skipped": skipped}
