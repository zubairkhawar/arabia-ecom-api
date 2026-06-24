from typing import Optional, Union
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from .db import get_db
from .security import decode_jwt
from .models import Reseller, AdminUser


def _bearer(authorization: Optional[str]) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.split(" ", 1)[1]
    payload = decode_jwt(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return payload


def get_current_reseller(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> Reseller:
    """Returns the logged-in Reseller. Admin tokens are rejected here —
    admins must use admin-only endpoints."""
    payload = _bearer(authorization)
    kind = payload.get("kind", "reseller")
    if kind != "reseller":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This endpoint is for resellers only")
    user = db.get(Reseller, payload.get("sub"))
    if not user or user.status == "suspended":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or suspended")
    return user


def require_admin(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> AdminUser:
    """Returns the logged-in AdminUser. Only the sole-admin token works.
    Reseller tokens are rejected even if their role used to be 'admin'."""
    payload = _bearer(authorization)
    if payload.get("kind") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    admin = db.get(AdminUser, payload.get("sub"))
    if not admin or not admin.enabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Admin not found or disabled")
    return admin


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> Union[Reseller, AdminUser]:
    """Accept either kind. Useful for /auth/me."""
    payload = _bearer(authorization)
    kind = payload.get("kind", "reseller")
    if kind == "admin":
        a = db.get(AdminUser, payload.get("sub"))
        if not a or not a.enabled:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Admin not found")
        return a
    r = db.get(Reseller, payload.get("sub"))
    if not r or r.status == "suspended":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return r
