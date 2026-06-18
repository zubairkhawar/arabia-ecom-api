"""Universal-number pool router.
Assigns a reseller to a pool number with 50/number capacity. When a number
fills, the next active number in the same country opens automatically.
"""
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..models import PoolNumber, PoolAssignment, Reseller, WhatsAppConfig


def get_or_assign(db: Session, reseller: Reseller) -> Optional[PoolNumber]:
    """Return the PoolNumber a reseller is on. Auto-assign if missing."""
    existing = db.execute(
        select(PoolAssignment).where(PoolAssignment.reseller_id == reseller.id)
    ).scalar_one_or_none()
    if existing:
        return db.get(PoolNumber, existing.pool_number_id)

    # Find an active number in the reseller's country with room.
    # Order by `assigned` desc so we fill the most-loaded number first
    # (clean spillover) — `.first()` returns one or None.
    candidate = db.execute(
        select(PoolNumber)
        .where(
            PoolNumber.country_code == reseller.country,
            PoolNumber.status == "active",
            PoolNumber.assigned < PoolNumber.capacity,
        )
        .order_by(PoolNumber.assigned.desc())
        .limit(1)
    ).scalars().first()

    if not candidate:
        return None

    assignment = PoolAssignment(reseller_id=reseller.id, pool_number_id=candidate.id)
    db.add(assignment)
    candidate.assigned += 1
    if candidate.assigned >= candidate.capacity:
        candidate.status = "full"
    db.flush()
    return candidate


def resolve_wa_target(db: Session, reseller: Reseller) -> Optional[str]:
    """The WhatsApp number a customer should message for this reseller.
    Returns the display number (E.164) suitable for wa.me/."""
    cfg = db.execute(
        select(WhatsAppConfig).where(WhatsAppConfig.reseller_id == reseller.id)
    ).scalar_one_or_none()
    if cfg and cfg.number_type == "own" and cfg.display_phone_number:
        return cfg.display_phone_number
    if cfg and cfg.number_type == "universal":
        n = get_or_assign(db, reseller)
        if n:
            return n.number
    # fallback: any pool number in country
    n = get_or_assign(db, reseller)
    return n.number if n else None
