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

@router.get("/meta-config", response_model=MetaConfigOut)
def get_meta_config(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    cfg = db.execute(select(MetaConfig).where(MetaConfig.reseller_id == current.id)).scalar_one_or_none()
    if not cfg:
        return MetaConfigOut(pixel_id=None, has_token=False, test_event_code=None, verified=False)
    return MetaConfigOut(
        pixel_id=cfg.pixel_id,
        has_token=bool(cfg.capi_access_token_enc),
        test_event_code=cfg.test_event_code,
        verified=cfg.verified,
    )


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
        cfg.pixel_id = payload.pixel_id
    if payload.capi_access_token:
        cfg.capi_access_token_enc = encrypt(payload.capi_access_token)
    if payload.test_event_code is not None:
        cfg.test_event_code = payload.test_event_code or None
    cfg.verified = bool(cfg.pixel_id and cfg.capi_access_token_enc)
    db.commit()
    db.refresh(cfg)
    return MetaConfigOut(
        pixel_id=cfg.pixel_id,
        has_token=bool(cfg.capi_access_token_enc),
        test_event_code=cfg.test_event_code,
        verified=cfg.verified,
    )


# ---------- WhatsApp config ----------

@router.get("/wa-config", response_model=WhatsAppConfigOut)
def get_wa_config(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    cfg = db.execute(select(WhatsAppConfig).where(WhatsAppConfig.reseller_id == current.id)).scalar_one_or_none()
    if not cfg:
        return WhatsAppConfigOut(
            number_type="own", waba_id=None, phone_number_id=None,
            display_phone_number=None, has_token=False, verified=False,
        )
    return WhatsAppConfigOut(
        number_type=cfg.number_type,
        waba_id=cfg.waba_id,
        phone_number_id=cfg.phone_number_id,
        display_phone_number=cfg.display_phone_number,
        has_token=bool(cfg.access_token_enc),
        verified=cfg.verified,
    )


@router.put("/wa-config", response_model=WhatsAppConfigOut)
def upsert_wa_config(
    payload: WhatsAppConfigIn,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    if payload.number_type not in ("own", "universal"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "number_type must be 'own' or 'universal'")
    cfg = db.execute(select(WhatsAppConfig).where(WhatsAppConfig.reseller_id == current.id)).scalar_one_or_none()
    if not cfg:
        cfg = WhatsAppConfig(reseller_id=current.id)
        db.add(cfg)
    cfg.number_type = payload.number_type
    if payload.number_type == "own":
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
        cfg.verified = all([cfg.waba_id, cfg.phone_number_id, cfg.access_token_enc])
    else:
        # universal — auto-assign pool number on first save (handled in pool router on demand)
        cfg.verified = True
    db.commit()
    db.refresh(cfg)
    return WhatsAppConfigOut(
        number_type=cfg.number_type,
        waba_id=cfg.waba_id,
        phone_number_id=cfg.phone_number_id,
        display_phone_number=cfg.display_phone_number,
        has_token=bool(cfg.access_token_enc),
        verified=cfg.verified,
    )
