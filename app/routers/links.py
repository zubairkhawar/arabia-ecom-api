"""Public link resolver + click endpoint.

Flow:
1. Frontend (Vercel) hosts `/r/[slug]` page.
2. On page load: GET /links/resolve/{slug} → product info + pixel_id + wa_deeplink.
3. Page injects the reseller's Meta Pixel snippet, fires the top-of-funnel
   event (default InitiateCheckout) with an event_id.
4. Page POSTs to /links/click with the click context → backend records
   click_session + mirrors the event via Meta CAPI server-side (same event_id
   so Meta dedupes). For non-Meta platforms the event is logged as 'skipped'
   for Phase 1.5.
5. Page POSTs to /links/pixel-fired with the click_session_id (sendBeacon,
   best-effort) to mark that the browser pixel beacon flushed.
6. Page redirects to wa.me/{number}?text=Hi%20I'm%20interested%20...%20[ref:c_xxxxxxxx]
"""
import re
import time
import urllib.parse
from collections import defaultdict, deque
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..db import get_db
from ..models import Product, Reseller, ClickSession, MetaConfig, PoolNumber, PoolAssignment, WhatsAppConfig
from ..services.pool_router import resolve_wa_target, get_or_assign
from ..services.slug import ref_token as gen_ref
from ..services.attribution import dispatch_top_of_funnel
from ..services import meta_capi
from ..schemas.links import LinkResolveOut, ClickIn, ClickOut, PixelFiredIn

router = APIRouter(prefix="/links", tags=["links"])


# crawler / link-preview UAs we should not log as real clicks
_BOT_UA = re.compile(
    r"(facebookexternalhit|whatsapp|telegrambot|twitterbot|slackbot|"
    r"linkedinbot|discordbot|googlebot|bingbot|baiduspider|yandex|"
    r"applebot|pinterest|skypeuripreview|preview|spider|crawler|bot)",
    re.I,
)


# tiny in-memory rate limiter (per-IP, sliding window).
# Render rolling restart is fine — this is best-effort, not security.
_RATE_WINDOW = 60.0
_RATE_MAX = 30
_rate_log: "dict[str, deque[float]]" = defaultdict(deque)


def _rate_check(ip: Optional[str]) -> bool:
    if not ip:
        return True
    now = time.time()
    dq = _rate_log[ip]
    while dq and now - dq[0] > _RATE_WINDOW:
        dq.popleft()
    if len(dq) >= _RATE_MAX:
        return False
    dq.append(now)
    return True


def _wa_deeplink(number: str, product_name: str, ref: str) -> str:
    """Build the wa.me deeplink. The [ref:token] suffix is the bridge from
    ad-click to chat: the WA webhook regex parses it to attribute the
    inbound message back to the click_session."""
    clean = number.replace("+", "").replace(" ", "").replace("-", "")
    text = f"Hi! I'm interested in {product_name} 🛍️\n[ref:{ref}]"
    return f"https://wa.me/{clean}?text={urllib.parse.quote(text)}"


def _resolve_pool_number(db: Session, reseller: Reseller) -> tuple[Optional[str], Optional[str]]:
    """Return (display_number, pool_number_id_or_None) for this reseller."""
    cfg = db.execute(
        select(WhatsAppConfig).where(WhatsAppConfig.reseller_id == reseller.id)
    ).scalar_one_or_none()
    if cfg and cfg.number_type == "own" and cfg.display_phone_number:
        return cfg.display_phone_number, None
    # universal — pick a slot from the pool
    n = get_or_assign(db, reseller)
    if n:
        return n.number, n.id
    # fallback: any number resolution
    fallback = resolve_wa_target(db, reseller)
    return fallback, None


@router.get("/resolve/{slug}", response_model=LinkResolveOut)
def resolve(slug: str, db: Session = Depends(get_db)):
    p = db.execute(select(Product).where(Product.slug == slug, Product.active == True)).scalar_one_or_none()
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    r = db.get(Reseller, p.reseller_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reseller not found")
    wa_target, _ = _resolve_pool_number(db, r)
    if not wa_target:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No WhatsApp number assigned to this reseller")
    meta_cfg = db.execute(select(MetaConfig).where(MetaConfig.reseller_id == r.id)).scalar_one_or_none()
    ref = gen_ref()
    db.commit()
    return LinkResolveOut(
        product_id=p.id,
        product_name=p.name,
        product_image=p.image_url,
        price=p.price,
        currency=p.currency,
        pixel_id=meta_cfg.pixel_id if meta_cfg else None,
        default_event=meta_cfg.default_event if meta_cfg and meta_cfg.default_event else "InitiateCheckout",
        wa_target_number=wa_target,
        wa_deeplink=_wa_deeplink(wa_target, p.name, ref),
        ref_token=ref,
        reseller_id=r.id,
        reseller_name=r.name,
    )


@router.post("/click", response_model=ClickOut)
async def click(
    payload: ClickIn,
    request: Request,
    db: Session = Depends(get_db),
):
    p = db.execute(select(Product).where(Product.slug == payload.slug, Product.active == True)).scalar_one_or_none()
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    r = db.get(Reseller, p.reseller_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reseller not found")

    client_ip = request.client.host if request.client else None
    ua = payload.user_agent or request.headers.get("user-agent") or ""

    # Drop link-preview crawlers (Meta + WhatsApp + others). They hit our URL
    # to fetch the OG card; recording them as clicks would pollute attribution.
    if _BOT_UA.search(ua):
        return ClickOut(
            ref_token="", click_session_id="", event_id="",
            pixel_id=None, wa_deeplink="", capi_status="skipped",
            capi_response_code=None, bot=True,
        )

    if not _rate_check(client_ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limited")

    src = (payload.src_platform or "other").lower()
    if src not in ("tiktok", "meta", "snapchat", "google", "other"):
        src = "other"

    fbc = payload.fbc or (meta_capi.fbc_from_fbclid(payload.fbclid) if payload.fbclid else None)

    wa_number, pool_number_id = _resolve_pool_number(db, r)

    click_session = ClickSession(
        reseller_id=r.id,
        product_id=p.id,
        ref_token=gen_ref(),
        src_platform=src,
        fbclid=payload.fbclid,
        fbp=payload.fbp,
        fbc=fbc,
        ttclid=payload.ttclid,
        sclid=payload.sclid,
        gclid=payload.gclid,
        utm_source=payload.utm_source,
        utm_medium=payload.utm_medium,
        utm_campaign=payload.utm_campaign,
        utm_content=payload.utm_content,
        utm_term=payload.utm_term,
        ip=client_ip,
        user_agent=ua or None,
        referer=payload.referer or request.headers.get("referer"),
        landing_url=payload.landing_url,
        wa_number=wa_number,
        pool_number_id=pool_number_id,
    )
    db.add(click_session)
    db.flush()

    # Server-side mirror of the browser pixel top-of-funnel event
    meta_cfg = db.execute(select(MetaConfig).where(MetaConfig.reseller_id == r.id)).scalar_one_or_none()
    event_name = (meta_cfg.default_event if meta_cfg and meta_cfg.default_event else "InitiateCheckout")
    evt = await dispatch_top_of_funnel(db, r, click_session, p, event_name=event_name)

    deeplink = _wa_deeplink(wa_number, p.name, click_session.ref_token) if wa_number else ""

    db.commit()

    return ClickOut(
        ref_token=click_session.ref_token,
        click_session_id=click_session.id,
        event_id=evt.event_id,
        pixel_id=meta_cfg.pixel_id if meta_cfg else None,
        wa_deeplink=deeplink,
        capi_status=evt.status,
        capi_response_code=evt.response_code,
        bot=False,
    )


@router.post("/pixel-fired")
def pixel_fired(payload: PixelFiredIn, db: Session = Depends(get_db)):
    """Beacon target — frontend calls this after fbq().track() to mark
    that the browser pixel beacon actually flushed. Best-effort."""
    cs = db.get(ClickSession, payload.click_session_id)
    if not cs:
        return {"ok": False, "reason": "click_session not found"}
    cs.add_to_cart_sent = True  # repurposed flag — means "browser pixel confirmed"
    db.commit()
    return {"ok": True}
