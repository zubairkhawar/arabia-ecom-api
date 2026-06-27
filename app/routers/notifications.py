from typing import List, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, update, func
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_reseller
from ..models import Reseller, Notification

router = APIRouter(prefix="/me/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    id: str
    type: str
    title: str
    body: Optional[str] = None
    href: Optional[str] = None
    meta: Optional[dict] = None
    seen: bool
    created_at: datetime


class NotificationListOut(BaseModel):
    items: List[NotificationOut]
    unseen: int  # how many haven't been opened in the bell yet


@router.get("", response_model=NotificationListOut)
def list_notifications(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
    limit: int = 20,
):
    rows = db.execute(
        select(Notification)
        .where(Notification.reseller_id == current.id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    ).scalars().all()
    unseen = db.execute(
        select(func.count(Notification.id))
        .where(Notification.reseller_id == current.id, Notification.seen == False)  # noqa: E712
    ).scalar_one()
    return NotificationListOut(
        items=[NotificationOut.model_validate(n, from_attributes=True) for n in rows],
        unseen=int(unseen or 0),
    )


@router.post("/mark-seen", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_seen(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    """Called when the bell dropdown opens — clears the unread badge.
    Does NOT mark each item read individually (use /{id}/read for that)."""
    db.execute(
        update(Notification)
        .where(Notification.reseller_id == current.id, Notification.seen == False)  # noqa: E712
        .values(seen=True)
    )
    db.commit()


@router.post("/{notif_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(
    notif_id: str,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    n = db.get(Notification, notif_id)
    if not n or n.reseller_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "notification not found")
    n.read_at = datetime.now(timezone.utc).isoformat()
    n.seen = True
    db.commit()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear_all(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    db.execute(
        Notification.__table__.delete().where(Notification.reseller_id == current.id)
    )
    db.commit()
