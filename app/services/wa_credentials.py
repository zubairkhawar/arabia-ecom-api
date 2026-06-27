"""Resolve which WhatsApp credentials to use when sending a message
to a customer in a given chat.

Two paths:
  1. The chat originated from a UNIVERSAL POOL number — find via the
     chat's click_session.pool_number_id. Use the pool's WABA creds.
  2. The chat originated from the reseller's OWN number — use
     WhatsAppConfig for that reseller.

Returns (phone_number_id, access_token_enc) or (None, None) if no
credentials are configured yet (dev / stubbed sends will pass through).
"""
from typing import Optional, Tuple
from sqlalchemy.orm import Session

from ..models import Chat, ClickSession, PoolNumber, WhatsAppConfig


def resolve_send_creds(
    db: Session, chat: Chat
) -> Tuple[Optional[str], Optional[str]]:
    # Pool path: trace via click_session.pool_number_id
    if chat.click_session_id:
        cs = db.get(ClickSession, chat.click_session_id)
        if cs and cs.pool_number_id:
            pool = db.get(PoolNumber, cs.pool_number_id)
            if pool and pool.phone_number_id and pool.access_token_enc:
                return pool.phone_number_id, pool.access_token_enc

    # Reseller own-number path
    cfg = db.query(WhatsAppConfig).filter(
        WhatsAppConfig.reseller_id == chat.reseller_id
    ).first()
    if cfg and cfg.phone_number_id and cfg.access_token_enc:
        return cfg.phone_number_id, cfg.access_token_enc

    return None, None
