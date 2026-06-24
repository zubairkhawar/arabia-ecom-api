"""Ensure the sole platform admin exists and any legacy reseller-with-
admin-role is downgraded. Runs once at app startup."""
import logging
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import AdminUser, Reseller
from ..security import hash_password

log = logging.getLogger(__name__)


def ensure_sole_admin(db: Session) -> None:
    email = settings.admin_email.lower().strip()
    pwd = settings.admin_password

    # 1. Downgrade any reseller that had role='admin' (legacy).
    rogue = db.execute(
        select(Reseller).where(Reseller.role == "admin")
    ).scalars().all()
    for r in rogue:
        log.warning("downgrading legacy admin-role reseller %s", r.email)
        r.role = "reseller"

    # 2. If a Reseller exists with the protected admin email, delete it
    # (resellers can't share the admin email).
    rival = db.execute(select(Reseller).where(Reseller.email == email)).scalar_one_or_none()
    if rival:
        from .cleanup import hard_delete_reseller
        try:
            hard_delete_reseller(db, rival.id)
            log.warning("deleted legacy reseller account %s — admin email is reserved", email)
        except Exception:
            log.exception("could not delete legacy admin reseller %s", email)

    # 3. Make sure exactly one AdminUser exists for the configured email.
    existing = db.execute(select(AdminUser).where(AdminUser.email == email)).scalar_one_or_none()
    if existing:
        # Refresh password every restart so the env var is the source of truth
        existing.password_hash = hash_password(pwd)
        existing.enabled = True
        existing.level = "Owner"
        log.info("admin bootstrap: refreshed password for %s", email)
    else:
        admin = AdminUser(
            name="Platform Admin",
            email=email,
            password_hash=hash_password(pwd),
            level="Owner",
            enabled=True,
        )
        db.add(admin)
        log.info("admin bootstrap: created sole admin %s", email)

    # 4. Disable any other admin rows so there's truly only one admin.
    others = db.execute(select(AdminUser).where(AdminUser.email != email)).scalars().all()
    for o in others:
        if o.enabled:
            log.warning("admin bootstrap: disabling extra admin %s", o.email)
            o.enabled = False

    db.commit()
