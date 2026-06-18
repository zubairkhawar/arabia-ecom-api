from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..db import get_db
from ..deps import get_current_reseller
from ..models import Reseller, AISetting
from ..security import hash_password, verify_password, issue_jwt
from ..schemas.common import SignupIn, LoginIn, TokenResponse, ResellerOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupIn, db: Session = Depends(get_db)):
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
    )
    db.add(r)
    db.flush()
    db.add(AISetting(reseller_id=r.id))
    db.commit()
    db.refresh(r)
    return TokenResponse(access_token=issue_jwt(r.id, r.role), reseller=ResellerOut.model_validate(r))


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    r = db.execute(select(Reseller).where(Reseller.email == payload.email)).scalar_one_or_none()
    if not r or not verify_password(payload.password, r.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if r.status == "suspended":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account suspended")
    return TokenResponse(access_token=issue_jwt(r.id, r.role), reseller=ResellerOut.model_validate(r))


@router.get("/me", response_model=ResellerOut)
def me(current: Reseller = Depends(get_current_reseller)):
    return current
