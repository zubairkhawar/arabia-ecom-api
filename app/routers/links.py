"""Public link resolver + click endpoint.

Flow:
1. Frontend (Vercel) hosts `/r/[slug]` page.
2. On page load: GET /links/resolve/{slug} → product info + pixel_id + wa_deeplink target.
3. Page injects the reseller's Meta Pixel snippet, fires AddToCart with event_id.
4. Page POSTs to /links/click with the click context → backend records click_session +
   mirrors AddToCart via Meta CAPI server-side (dedupes via same event_id).
5. Page redirects to wa.me/{number}?text=Hi%20I'm%20interested%20in%20{product}%20[c_xxxxxxxx]
"""
import urllib.parse
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..db import get_db
from ..models import Product, Reseller, ClickSession, MetaConfig
from ..services.pool_router import resolve_wa_target
from ..services.slug import ref_token as gen_ref
from ..services.attribution import dispatch_add_to_cart
from ..services import meta_capi
from ..schemas.links import LinkResolveOut, ClickIn, ClickOut

router = APIRouter(prefix="/links", tags=["links"])


def _wa_deeplink(number: str, product_name: str, ref: str) -> str:
    clean = number.replace("+", "").replace(" ", "").replace("-", "")
    text = f"Hi! I'm interested in {product_name} [{ref}]"
    return f"https://wa.me/{clean}?text={urllib.parse.quote(text)}"


@router.get("/resolve/{slug}", response_model=LinkResolveOut)
def resolve(slug: str, db: Session = Depends(get_db)):
    p = db.execute(select(Product).where(Product.slug == slug, Product.active == True)).scalar_one_or_none()
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    r = db.get(Reseller, p.reseller_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reseller not found")
    wa_target = resolve_wa_target(db, r)
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

    src = (payload.src_platform or "other").lower()
    if src not in ("tiktok", "meta", "snapchat", "google", "other"):
        src = "other"

    fbc = meta_capi.fbc_from_fbclid(payload.fbclid) if payload.fbclid else None

    # Build the click session
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
        ip=request.client.host if request.client else None,
        user_agent=payload.user_agent or request.headers.get("user-agent"),
        referer=payload.referer or request.headers.get("referer"),
    )
    db.add(click_session)
    db.flush()

    # Fire (mirror) AddToCart server-side
    evt = await dispatch_add_to_cart(db, r, click_session, p)

    # Resolve WA target + deeplink with the ref token
    wa_target = resolve_wa_target(db, r)
    deeplink = _wa_deeplink(wa_target, p.name, click_session.ref_token) if wa_target else ""

    meta_cfg = db.execute(select(MetaConfig).where(MetaConfig.reseller_id == r.id)).scalar_one_or_none()

    db.commit()

    return ClickOut(
        ref_token=click_session.ref_token,
        click_session_id=click_session.id,
        event_id=evt.event_id,
        pixel_id=meta_cfg.pixel_id if meta_cfg else None,
        wa_deeplink=deeplink,
        capi_status=evt.status,
        capi_response_code=evt.response_code,
    )
