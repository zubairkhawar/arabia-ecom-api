"""HTTP-triggered cron endpoints. Designed to be called by an external
scheduler (Render Cron Jobs, cron-job.org, GitHub Actions). All endpoints
require the X-Cron-Token header to match settings.cron_token."""
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..services import billing as billing_service

router = APIRouter(prefix="/_cron", tags=["cron"])


def _verify_token(x_cron_token: str = Header(default="")) -> None:
    if not settings.cron_token or x_cron_token != settings.cron_token:
        raise HTTPException(401, "invalid cron token")


@router.post("/billing-tick", dependencies=[Depends(_verify_token)])
def billing_tick(db: Session = Depends(get_db)):
    """Run nightly. Expires trials past their window and rolls active
    subscriptions whose period ended (refills credits). Idempotent."""
    expired = billing_service.expire_trials(db)
    rolled = billing_service.roll_periods(db)
    db.commit()
    return {
        "ok": True,
        "trials_expired": expired,
        "subscriptions_rolled": rolled,
    }
