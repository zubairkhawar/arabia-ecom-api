"""Notification helper. One-call creator used by the webhook + order
service + (later) other event sources."""
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from ..models import Notification


def create_notification(
    db: Session,
    *,
    reseller_id: Optional[str],
    type: str,
    title: str,
    body: Optional[str] = None,
    href: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Notification:
    n = Notification(
        reseller_id=reseller_id,
        type=type,
        title=title,
        body=body,
        href=href,
        meta=meta or {},
    )
    db.add(n)
    db.flush()
    return n
