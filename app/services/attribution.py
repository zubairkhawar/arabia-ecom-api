"""Attribution dispatcher.

Phase 1 fires real Meta CAPI events. Other platforms (TikTok, Snap, Google)
are recorded in `attribution_events` with status='skipped' for now — the
interface is here so wiring them in Phase 1.5 is just a matter of filling
in the actual API call.
"""
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session

from ..models import (
    AttributionEvent,
    ClickSession,
    Order,
    MetaConfig,
    Reseller,
    OrderItem,
    Product,
)
from ..security import decrypt
from . import meta_capi


async def dispatch_top_of_funnel(
    db: Session,
    reseller: Reseller,
    click: ClickSession,
    product: Product,
    event_name: str = "InitiateCheckout",
) -> AttributionEvent:
    """Server-side mirror of the browser Pixel top-of-funnel event
    (InitiateCheckout / AddToCart / ViewContent / Lead).

    Uses a stored event_id so a subsequent browser pixel call with the
    same eventID is deduped by Meta."""
    if click.add_to_cart_event_id:
        event_id = click.add_to_cart_event_id
    else:
        event_id = meta_capi.gen_event_id()
        click.add_to_cart_event_id = event_id

    if click.src_platform == "meta":
        return await _send_meta(
            db, reseller, click, order=None,
            event_name=event_name,
            event_id=event_id,
            value=product.price,
            currency=product.currency,
            content_ids=[product.id],
            contents=[{"id": product.id, "quantity": 1, "item_price": product.price}],
        )
    else:
        evt = AttributionEvent(
            reseller_id=reseller.id,
            click_session_id=click.id,
            platform=click.src_platform,
            event_name=event_name,
            event_id=event_id,
            value=product.price,
            currency=product.currency,
            status="skipped",
            payload={"reason": f"{click.src_platform} CAPI not implemented yet (Phase 1.5)"},
        )
        db.add(evt)
        return evt


# Back-compat alias — older tests / callers may still import this
dispatch_add_to_cart = dispatch_top_of_funnel


async def dispatch_purchase(
    db: Session,
    reseller: Reseller,
    order: Order,
) -> Optional[AttributionEvent]:
    if order.purchase_event_sent:
        return None
    if not order.click_session_id:
        evt = AttributionEvent(
            reseller_id=reseller.id,
            order_id=order.id,
            platform="meta",
            event_name="Purchase",
            event_id=meta_capi.gen_event_id(),
            value=order.amount,
            currency=order.currency,
            status="skipped",
            payload={"reason": "no click_session"},
        )
        db.add(evt)
        order.purchase_event_sent = True
        return evt

    click = db.get(ClickSession, order.click_session_id)
    if not click:
        return None

    event_id = meta_capi.gen_event_id()
    click.purchase_event_id = event_id

    items: List[OrderItem] = list(order.items)
    contents = [
        {"id": it.product_id, "quantity": it.qty, "item_price": it.unit_price}
        for it in items
    ]
    content_ids = [it.product_id for it in items]

    if click.src_platform == "meta":
        result = await _send_meta(
            db, reseller, click, order,
            event_name="Purchase",
            event_id=event_id,
            value=order.amount,
            currency=order.currency,
            content_ids=content_ids,
            contents=contents,
        )
    else:
        result = AttributionEvent(
            reseller_id=reseller.id,
            click_session_id=click.id,
            order_id=order.id,
            platform=click.src_platform,
            event_name="Purchase",
            event_id=event_id,
            value=order.amount,
            currency=order.currency,
            status="skipped",
            payload={"reason": f"{click.src_platform} CAPI not implemented yet (Phase 1.5)"},
        )
        db.add(result)

    order.purchase_event_sent = True
    return result


async def send_test_event(
    db: Session, reseller: Reseller, cfg: MetaConfig
) -> Dict[str, Any]:
    """Fire a no-value test InitiateCheckout to Meta CAPI to verify the
    credentials. Returns the raw response details for the UI."""
    if not cfg.pixel_id or not cfg.capi_access_token_enc:
        return {"ok": False, "status": 0, "body": "missing pixel_id or access_token"}
    event_id = meta_capi.gen_event_id()
    payload = meta_capi.build_event(
        event_name="InitiateCheckout",
        event_id=event_id,
        fbp=None, fbc=None,
        client_ip=None, client_ua="arabia-ecom-api/verify",
        action_source=cfg.action_source or "website",
    )
    result = await meta_capi.send_event(
        pixel_id=cfg.pixel_id,
        access_token=decrypt(cfg.capi_access_token_enc),
        event=payload,
        test_event_code=cfg.test_event_code,
    )
    ok = 200 <= result.get("status", 0) < 300
    # log it
    db.add(AttributionEvent(
        reseller_id=reseller.id,
        platform="meta",
        event_name="InitiateCheckout",
        event_id=event_id,
        status="sent" if ok else "failed",
        response_code=result.get("status"),
        response_body=(result.get("body") or "")[:2000],
        payload=payload,
    ))
    return {"ok": ok, **result}


async def _send_meta(
    db: Session,
    reseller: Reseller,
    click: ClickSession,
    order: Optional[Order],
    event_name: str,
    event_id: str,
    value: float,
    currency: str,
    content_ids: List[str],
    contents: List[Dict[str, Any]],
) -> AttributionEvent:
    cfg = db.query(MetaConfig).filter(MetaConfig.reseller_id == reseller.id).first()

    if not cfg or not cfg.pixel_id or not cfg.capi_access_token_enc:
        evt = AttributionEvent(
            reseller_id=reseller.id,
            click_session_id=click.id,
            order_id=order.id if order else None,
            platform="meta",
            event_name=event_name,
            event_id=event_id,
            value=value,
            currency=currency,
            status="skipped",
            payload={"reason": "no MetaConfig (pixel_id or access_token missing)"},
        )
        db.add(evt)
        return evt

    customer_phone = None
    customer_email = None
    if order and order.customer_id:
        from ..models import Customer
        cust = db.get(Customer, order.customer_id)
        if cust:
            customer_phone = cust.phone
            customer_email = cust.email

    payload = meta_capi.build_event(
        event_name=event_name,
        event_id=event_id,
        fbp=click.fbp,
        fbc=click.fbc or meta_capi.fbc_from_fbclid(click.fbclid),
        client_ip=click.ip,
        client_ua=click.user_agent,
        phone=customer_phone,
        email=customer_email,
        value=value,
        currency=currency,
        content_ids=content_ids,
        contents=contents,
        event_source_url=click.landing_url or click.referer,
        action_source=cfg.action_source or "website",
    )

    access_token = decrypt(cfg.capi_access_token_enc)
    result = await meta_capi.send_event(
        pixel_id=cfg.pixel_id,
        access_token=access_token,
        event=payload,
        test_event_code=cfg.test_event_code,
    )

    status = "sent" if 200 <= result.get("status", 0) < 300 else "failed"
    evt = AttributionEvent(
        reseller_id=reseller.id,
        click_session_id=click.id,
        order_id=order.id if order else None,
        platform="meta",
        event_name=event_name,
        event_id=event_id,
        value=value,
        currency=currency,
        status=status,
        response_code=result.get("status"),
        response_body=(result.get("body") or "")[:2000],
        payload=payload,
    )
    db.add(evt)
    if event_name == "InitiateCheckout":
        click.add_to_cart_sent = (status == "sent")
    return evt
