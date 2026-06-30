"""Sync Shopify products → our products table.

Each Shopify product becomes (or updates) one Product row tagged with
shopify_store_id + shopify_product_id. Re-running the sync upserts.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Customer, Order, OrderItem, Product, Reseller, ShopifyStore,
)
from .shopify_client import fetch_orders, fetch_products
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


# ---------- orders ----------


def _map_status(sp_order: Dict[str, Any]) -> str:
    """Shopify financial_status + cancelled_at → our Order.status."""
    if sp_order.get("cancelled_at"):
        return "cancelled"
    fin = (sp_order.get("financial_status") or "").lower()
    if fin in ("paid", "partially_paid"):
        return "confirmed"
    if fin == "voided":
        return "cancelled"
    return "processing"


def _map_delivery(sp_order: Dict[str, Any]) -> str:
    """Shopify fulfillment_status → our Order.delivery_status.
    'fulfilled' = label printed / order shipped (NOT delivered — true
    delivery requires courier tracking integration)."""
    fs = (sp_order.get("fulfillment_status") or "").lower()
    if fs == "fulfilled":
        return "dispatched"
    if fs == "partial":
        return "dispatched"
    return "pending"


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # Shopify uses ISO 8601 with timezone offset
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _customer_key(sp_customer: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract phone for Customer dedup. Returns None if no phone — caller
    skips the order with a no_phone count (per ratified decision #2a)."""
    if not sp_customer:
        return None
    phone = (sp_customer.get("phone") or "").strip()
    return phone or None


def _find_or_create_customer(
    db: Session, reseller: Reseller, sp_customer: Dict[str, Any], phone: str
) -> Customer:
    existing = db.execute(
        select(Customer).where(
            Customer.reseller_id == reseller.id,
            Customer.phone == phone,
        )
    ).scalar_one_or_none()
    if existing:
        # Patch in any newer details, but don't overwrite name if already set.
        if not existing.name and sp_customer:
            first = (sp_customer.get("first_name") or "").strip()
            last = (sp_customer.get("last_name") or "").strip()
            full = " ".join(p for p in (first, last) if p)
            if full:
                existing.name = full
        if not existing.email and sp_customer.get("email"):
            existing.email = sp_customer["email"]
        return existing
    first = (sp_customer.get("first_name") or "").strip()
    last = (sp_customer.get("last_name") or "").strip()
    full = " ".join(p for p in (first, last) if p) or None
    c = Customer(
        reseller_id=reseller.id,
        name=full,
        phone=phone,
        email=sp_customer.get("email"),
    )
    db.add(c)
    db.flush()
    return c


def _find_or_create_product(
    db: Session, reseller: Reseller, store: ShopifyStore, sp_line_item: Dict[str, Any]
) -> Tuple[Optional[Product], bool]:
    """Resolve OrderItem.product_id. If the Shopify product hasn't been
    synced (e.g. the seller never ran products sync), auto-create a stub
    Product so the OrderItem FK is satisfied. The shopify:{store.name}
    source tag plus active=False flag it for the seller to review.

    Returns (product, was_created)."""
    sp_pid = sp_line_item.get("product_id")
    if not sp_pid:
        return (None, False)
    sp_pid_str = str(sp_pid)
    existing = db.execute(
        select(Product).where(
            Product.reseller_id == reseller.id,
            Product.shopify_store_id == store.id,
            Product.shopify_product_id == sp_pid_str,
        )
    ).scalar_one_or_none()
    if existing:
        return (existing, False)
    title = sp_line_item.get("title") or sp_line_item.get("name") or f"Shopify item {sp_pid_str}"
    try:
        price = float(sp_line_item.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0
    p = Product(
        reseller_id=reseller.id,
        name=title,
        slug=short_slug(),
        description="Auto-imported from a Shopify order. Review and complete before publishing.",
        price=price,
        currency=reseller.currency,
        country=reseller.country,
        channels=["shopify"],
        source=f"shopify:{store.name}",
        shopify_store_id=store.id,
        shopify_product_id=sp_pid_str,
        active=False,
    )
    db.add(p)
    db.flush()
    return (p, True)


def _upsert_order(
    db: Session, reseller: Reseller, store: ShopifyStore, sp_order: Dict[str, Any]
) -> Tuple[str, int]:
    """Upsert one Shopify order. Returns ("created"|"updated"|"skipped_no_phone"
    |"skipped_no_items", 0 or count of unknown_product_autocreated)."""
    sp_oid = str(sp_order.get("id") or "")
    if not sp_oid:
        return ("skipped_no_phone", 0)  # malformed Shopify response

    sp_customer = sp_order.get("customer") or {}
    phone = _customer_key(sp_customer)
    if not phone:
        return ("skipped_no_phone", 0)

    customer = _find_or_create_customer(db, reseller, sp_customer, phone)

    # Resolve line items + auto-create unknown products.
    line_items = sp_order.get("line_items") or []
    if not line_items:
        return ("skipped_no_items", 0)

    autocreated = 0
    resolved_items = []
    for li in line_items:
        prod, was_created = _find_or_create_product(db, reseller, store, li)
        if not prod:
            continue
        if was_created:
            autocreated += 1
        try:
            unit_price = float(li.get("price") or 0)
        except (TypeError, ValueError):
            unit_price = 0.0
        qty = int(li.get("quantity") or 1)
        resolved_items.append((prod.id, qty, unit_price, unit_price * qty))

    if not resolved_items:
        return ("skipped_no_items", autocreated)

    try:
        total = float(sp_order.get("total_price") or 0)
    except (TypeError, ValueError):
        total = sum(line_total for _, _, _, line_total in resolved_items)

    currency = sp_order.get("currency") or reseller.currency
    created_at = _parse_dt(sp_order.get("created_at"))
    cancelled_at = _parse_dt(sp_order.get("cancelled_at"))
    status = _map_status(sp_order)
    delivery_status = _map_delivery(sp_order)
    confirmed_at = _parse_dt(sp_order.get("processed_at")) if status == "confirmed" else None

    existing = db.execute(
        select(Order).where(
            Order.shopify_store_id == store.id,
            Order.shopify_order_id == sp_oid,
        )
    ).scalar_one_or_none()

    if existing:
        # Refresh mutable fields; leave items alone to avoid wiping any
        # manual edits the seller made through the orders UI.
        existing.status = status
        existing.delivery_status = delivery_status
        existing.amount = total
        existing.currency = currency
        existing.customer_address = sp_order.get("shipping_address") or sp_order.get("billing_address")
        if confirmed_at and not existing.confirmed_at:
            existing.confirmed_at = confirmed_at
        return ("updated", autocreated)

    order = Order(
        reseller_id=reseller.id,
        customer_id=customer.id,
        chat_id=None,
        click_session_id=None,
        code=f"SHP-{sp_oid}",
        amount=total,
        currency=currency,
        channel="shopify",
        status=status,
        delivery_status=delivery_status,
        source=f"shopify:{store.name}",
        source_platform=None,
        customer_address=sp_order.get("shipping_address") or sp_order.get("billing_address"),
        confirmed_at=confirmed_at,
        shopify_store_id=store.id,
        shopify_order_id=sp_oid,
    )
    if created_at:
        order.created_at = created_at
    db.add(order)
    db.flush()
    for product_id, qty, unit_price, line_total in resolved_items:
        db.add(OrderItem(
            order_id=order.id,
            product_id=product_id,
            qty=qty,
            unit_price=unit_price,
            line_total=line_total,
        ))
    return ("created", autocreated)


async def sync_orders(
    db: Session, reseller: Reseller, store: ShopifyStore,
    since: Optional[datetime] = None,
) -> Dict[str, int]:
    """Pull orders from Shopify and upsert them as Order rows.

    Strategy:
    - `since` precedence: explicit arg > store.last_orders_sync_at > 90d ago.
    - Per-order commit so partial progress survives transient failures.
    - store.last_orders_sync_at is advanced ONLY on a fully successful
      pass. If fetch_orders raises mid-flight (rate-limit, network), the
      cursor stays put; retry re-pulls the same window and the partial
      unique index on (shopify_store_id, shopify_order_id) makes the
      already-written rows idempotent (update branch, not insert).
    """
    if since is None:
        since = store.last_orders_sync_at or (
            datetime.now(timezone.utc) - timedelta(days=90)
        )

    items = await fetch_orders(
        store.shop_domain, store.access_token_enc, store.api_version, since=since
    )

    created = updated = skipped_no_phone = skipped_no_items = autocreated_products = 0
    for sp in items:
        try:
            outcome, ac = _upsert_order(db, reseller, store, sp)
        except Exception:
            db.rollback()
            raise
        autocreated_products += ac
        if outcome == "created":
            created += 1
            db.commit()
        elif outcome == "updated":
            updated += 1
            db.commit()
        elif outcome == "skipped_no_phone":
            skipped_no_phone += 1
        elif outcome == "skipped_no_items":
            skipped_no_items += 1

    # Full pass succeeded — advance the cursor.
    store.orders_synced = (store.orders_synced or 0) + created
    store.last_orders_sync_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "fetched": len(items),
        "created": created,
        "updated": updated,
        "skipped_no_phone": skipped_no_phone,
        "skipped_no_items": skipped_no_items,
        "autocreated_products": autocreated_products,
    }
