from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..db import get_db
from ..deps import get_current_reseller
from ..models import Reseller, AISetting, MetaConfig, WhatsAppConfig
from ..security import encrypt
from ..schemas.common import (
    ResellerOut,
    AISettingsOut,
    AISettingsUpdate,
    MetaConfigIn,
    MetaConfigOut,
    WhatsAppConfigIn,
    WhatsAppConfigOut,
)

router = APIRouter(prefix="/me", tags=["reseller"])


@router.get("", response_model=ResellerOut)
def get_me(current: Reseller = Depends(get_current_reseller)):
    return current


@router.get("/ai-settings", response_model=AISettingsOut)
def get_ai_settings(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    s = db.execute(select(AISetting).where(AISetting.reseller_id == current.id)).scalar_one_or_none()
    if not s:
        s = AISetting(reseller_id=current.id)
        db.add(s)
        db.commit()
        db.refresh(s)
    return s


@router.patch("/ai-settings", response_model=AISettingsOut)
def update_ai_settings(
    payload: AISettingsUpdate,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    s = db.execute(select(AISetting).where(AISetting.reseller_id == current.id)).scalar_one_or_none()
    if not s:
        s = AISetting(reseller_id=current.id)
        db.add(s)
        db.flush()
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return s


# ---------- Meta Pixel + CAPI config ----------

def _serialize_meta(cfg: MetaConfig | None) -> MetaConfigOut:
    if not cfg:
        return MetaConfigOut(
            pixel_id=None, has_token=False, test_event_code=None,
            default_event="InitiateCheckout", action_source="website",
            is_pixel_verified=False, is_capi_verified=False, verified=False,
        )
    return MetaConfigOut(
        pixel_id=cfg.pixel_id,
        has_token=bool(cfg.capi_access_token_enc),
        test_event_code=cfg.test_event_code,
        default_event=cfg.default_event or "InitiateCheckout",
        action_source=cfg.action_source or "website",
        is_pixel_verified=cfg.is_pixel_verified,
        is_capi_verified=cfg.is_capi_verified,
        verified=cfg.verified,
    )


@router.get("/meta-config", response_model=MetaConfigOut)
def get_meta_config(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    cfg = db.execute(select(MetaConfig).where(MetaConfig.reseller_id == current.id)).scalar_one_or_none()
    return _serialize_meta(cfg)


@router.put("/meta-config", response_model=MetaConfigOut)
def upsert_meta_config(
    payload: MetaConfigIn,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    cfg = db.execute(select(MetaConfig).where(MetaConfig.reseller_id == current.id)).scalar_one_or_none()
    if not cfg:
        cfg = MetaConfig(reseller_id=current.id)
        db.add(cfg)
    if payload.pixel_id is not None:
        cfg.pixel_id = payload.pixel_id or None
        cfg.is_pixel_verified = False
    if payload.capi_access_token:
        cfg.capi_access_token_enc = encrypt(payload.capi_access_token)
        cfg.is_capi_verified = False
    if payload.test_event_code is not None:
        cfg.test_event_code = payload.test_event_code or None
    if payload.default_event:
        if payload.default_event not in ("InitiateCheckout", "AddToCart", "ViewContent", "Lead"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "default_event must be InitiateCheckout|AddToCart|ViewContent|Lead")
        cfg.default_event = payload.default_event
    if payload.action_source:
        if payload.action_source not in ("website", "business_messaging"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "action_source must be website|business_messaging")
        cfg.action_source = payload.action_source
    cfg.verified = bool(cfg.pixel_id and cfg.capi_access_token_enc)
    db.commit()
    db.refresh(cfg)
    return _serialize_meta(cfg)


@router.post("/meta-config/verify")
async def verify_meta_config(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    """Send a test InitiateCheckout to Meta CAPI to confirm the credentials
    work. On success, flips is_capi_verified=true. Returns raw Meta response
    for surface in the UI."""
    cfg = db.execute(select(MetaConfig).where(MetaConfig.reseller_id == current.id)).scalar_one_or_none()
    if not cfg or not cfg.pixel_id or not cfg.capi_access_token_enc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Save pixel_id + capi_access_token first")
    from ..services.attribution import send_test_event
    result = await send_test_event(db, current, cfg)
    if result["ok"]:
        cfg.is_capi_verified = True
        cfg.verified = True
    db.commit()
    return {
        "ok": result["ok"],
        "capi_status": result.get("status", 0),
        "capi_response": (result.get("body") or "")[:1500],
        "pixel_id": cfg.pixel_id,
        "verified": cfg.is_capi_verified,
    }


@router.delete("/meta-config", status_code=status.HTTP_204_NO_CONTENT)
def delete_meta_config(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    cfg = db.execute(select(MetaConfig).where(MetaConfig.reseller_id == current.id)).scalar_one_or_none()
    if cfg:
        db.delete(cfg)
        db.commit()


# ---------- WhatsApp config ----------


def _serialize_wa(db: Session, current: Reseller, cfg: Optional[WhatsAppConfig]) -> WhatsAppConfigOut:
    """Serialize the WhatsApp config + lazily look up the assigned pool
    number when number_type='universal'."""
    if not cfg:
        return WhatsAppConfigOut(
            number_type="own", waba_id=None, phone_number_id=None,
            display_phone_number=None, has_token=False, verified=False,
        )
    pool_number = None
    pool_country = None
    if cfg.number_type == "universal":
        from ..models import PoolAssignment, PoolNumber
        assign = db.execute(
            select(PoolAssignment).where(PoolAssignment.reseller_id == current.id)
        ).scalar_one_or_none()
        if assign:
            pn = db.get(PoolNumber, assign.pool_number_id)
            if pn:
                pool_number = pn.number
                pool_country = pn.country
    return WhatsAppConfigOut(
        number_type=cfg.number_type,
        waba_id=cfg.waba_id,
        phone_number_id=cfg.phone_number_id,
        display_phone_number=cfg.display_phone_number,
        has_token=bool(cfg.access_token_enc),
        verified=cfg.verified,
        assigned_pool_number=pool_number,
        assigned_pool_country=pool_country,
    )


def _release_pool_assignment(db: Session, reseller_id: str) -> None:
    """When a reseller disconnects from the universal pool (or switches
    to own number), free their pool slot and decrement the counter."""
    from ..models import PoolAssignment, PoolNumber
    a = db.execute(
        select(PoolAssignment).where(PoolAssignment.reseller_id == reseller_id)
    ).scalar_one_or_none()
    if not a:
        return
    pn = db.get(PoolNumber, a.pool_number_id)
    if pn and (pn.assigned or 0) > 0:
        pn.assigned -= 1
        if pn.status == "full":
            pn.status = "active"
    db.delete(a)
    db.flush()


@router.get("/wa-config", response_model=WhatsAppConfigOut)
def get_wa_config(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    cfg = db.execute(
        select(WhatsAppConfig).where(WhatsAppConfig.reseller_id == current.id)
    ).scalar_one_or_none()
    return _serialize_wa(db, current, cfg)


@router.put("/wa-config", response_model=WhatsAppConfigOut)
async def upsert_wa_config(
    payload: WhatsAppConfigIn,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    """Connect a WhatsApp number. Only ONE mode at a time:
      - 'own': clears any prior pool assignment
      - 'universal': clears any prior own-number creds
    If the reseller is already configured with a different mode, the
    previous setup is wiped on save.
    """
    if payload.number_type not in ("own", "universal"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "number_type must be 'own' or 'universal'")
    cfg = db.execute(
        select(WhatsAppConfig).where(WhatsAppConfig.reseller_id == current.id)
    ).scalar_one_or_none()
    if not cfg:
        cfg = WhatsAppConfig(reseller_id=current.id)
        db.add(cfg)
    cfg.number_type = payload.number_type

    if payload.number_type == "own":
        # Switching to own → release any pool slot we held
        _release_pool_assignment(db, current.id)

        if payload.waba_id is not None:
            cfg.waba_id = payload.waba_id
        if payload.phone_number_id is not None:
            cfg.phone_number_id = payload.phone_number_id
        if payload.display_phone_number is not None:
            cfg.display_phone_number = payload.display_phone_number
        if payload.access_token:
            cfg.access_token_enc = encrypt(payload.access_token)
        if payload.webhook_verify_token is not None:
            cfg.webhook_verify_token = payload.webhook_verify_token or None

        if cfg.phone_number_id and cfg.access_token_enc:
            from ..services.whatsapp_cloud import verify_creds
            from ..security import decrypt
            check = await verify_creds(cfg.phone_number_id, decrypt(cfg.access_token_enc))
            if not check["ok"]:
                db.rollback()
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Meta rejected the credentials (HTTP {check['status']}). "
                    f"Check the Phone Number ID + Access Token. Meta said: {check['body'][:200]}",
                )
            cfg.verified = True
            if not cfg.display_phone_number:
                import json as _json
                try:
                    body = _json.loads(check["body"])
                    if body.get("display_phone_number"):
                        cfg.display_phone_number = body["display_phone_number"]
                except Exception:
                    pass
        else:
            cfg.verified = False
    else:
        # Universal — wipe any own-number creds the reseller previously had
        cfg.waba_id = None
        cfg.phone_number_id = None
        cfg.display_phone_number = None
        cfg.access_token_enc = None
        cfg.webhook_verify_token = None
        cfg.verified = True
        # Eagerly assign a pool number so the reseller sees their number
        # immediately on connect instead of "after first link click".
        db.flush()
        from ..services.pool_router import get_or_assign
        assigned = get_or_assign(db, current)
        if not assigned:
            db.rollback()
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"No pool number available for {current.country} right now. Please contact support.",
            )
    db.commit()
    db.refresh(cfg)
    return _serialize_wa(db, current, cfg)


@router.delete("/wa-config", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_wa(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    """Disconnect any WhatsApp setup the reseller has.
    - Universal: releases their pool slot (decrements PoolNumber.assigned).
    - Own: wipes credentials.
    After this they're back to step-1 of the wizard.
    """
    _release_pool_assignment(db, current.id)
    cfg = db.execute(
        select(WhatsAppConfig).where(WhatsAppConfig.reseller_id == current.id)
    ).scalar_one_or_none()
    if cfg:
        db.delete(cfg)
        db.commit()
