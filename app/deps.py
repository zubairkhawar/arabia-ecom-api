from typing import Optional
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from .db import get_db
from .security import decode_jwt
from .models import Reseller


def get_current_reseller(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> Reseller:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.split(" ", 1)[1]
    payload = decode_jwt(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    user = db.get(Reseller, payload.get("sub"))
    if not user or user.status == "suspended":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or suspended")
    return user


def require_admin(reseller: Reseller = Depends(get_current_reseller)) -> Reseller:
    if reseller.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return reseller


def get_optional_reseller(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> Optional[Reseller]:
    if not authorization:
        return None
    try:
        return get_current_reseller(authorization, db)  # type: ignore
    except HTTPException:
        return None
