from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..config import settings
from ..db import get_db
from ..deps import get_current_user
from ..models import Reseller, AISetting, AdminUser
from ..security import hash_password, verify_password, issue_jwt
from ..schemas.common import SignupIn, LoginIn, TokenResponse, ResellerOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _is_protected_admin_email(email: str) -> bool:
    return email.lower().strip() == settings.admin_email.lower().strip()


def _admin_as_reseller_out(a: AdminUser) -> ResellerOut:
    """Adapt AdminUser to the shared TokenResponse shape so the frontend
    can render a session uniformly. role='admin' tells the UI which portal to enter."""
    return ResellerOut(
        id=a.id, name=a.name, email=a.email,
        plan="-", status="active" if a.enabled else "suspended",
        country="-", currency="-", role="admin",
        created_at=a.created_at,
    )


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupIn, db: Session = Depends(get_db)):
    if _is_protected_admin_email(payload.email):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This email is reserved for platform administration",
        )
    existing = db.execute(select(Reseller).where(Reseller.email == payload.email)).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    r = Reseller(
        name=payload.name,
        email=payload.email,
        password_hash=hash_password(payload.password),
        country=payload.country,
        currency=payload.currency,
        plan="silver",
        role="reseller",  # always reseller — admins are in admin_users
    )
    db.add(r)
    db.flush()
    db.add(AISetting(reseller_id=r.id))
    db.commit()
    db.refresh(r)
    return TokenResponse(
        access_token=issue_jwt(r.id, kind="reseller", role="reseller"),
        reseller=ResellerOut.model_validate(r),
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    """Single login endpoint. If the email matches the protected admin
    email, we authenticate against admin_users; otherwise against resellers."""
    if _is_protected_admin_email(payload.email):
        a = db.execute(select(AdminUser).where(AdminUser.email == payload.email)).scalar_one_or_none()
        if not a or not a.enabled or not verify_password(payload.password, a.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
        return TokenResponse(
            access_token=issue_jwt(a.id, kind="admin", role="admin"),
            reseller=_admin_as_reseller_out(a),
        )

    r = db.execute(select(Reseller).where(Reseller.email == payload.email)).scalar_one_or_none()
    if not r or not verify_password(payload.password, r.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if r.status == "suspended":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account suspended")
    # Force role to 'reseller' on every login. Even if some legacy row has
    # role='admin' in the resellers table, it's no longer respected.
    if r.role != "reseller":
        r.role = "reseller"
        db.commit()
    return TokenResponse(
        access_token=issue_jwt(r.id, kind="reseller", role="reseller"),
        reseller=ResellerOut.model_validate(r),
    )


@router.get("/me", response_model=ResellerOut)
def me(current=Depends(get_current_user)):
    """Works for both kinds; admins are returned with role='admin'."""
    if isinstance(current, AdminUser):
        return _admin_as_reseller_out(current)
    return current
