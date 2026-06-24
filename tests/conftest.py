"""Test fixtures.

The Render DB is shared (dev), so each fixture cleans up after itself in
FK-safe order via app.services.cleanup.hard_delete_reseller.
"""
import pytest
import secrets
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.main import app
from app.models import Reseller, AISetting, PoolNumber, AdminUser
from app.security import hash_password, issue_jwt
from app.services.cleanup import hard_delete_reseller


# Back-compat alias for older test files.
_hard_delete_reseller = hard_delete_reseller


@pytest.fixture()
def db() -> Session:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def reseller(db: Session) -> Reseller:
    email = f"test+{secrets.token_hex(4)}@example.com"
    r = Reseller(
        name="Test Reseller", email=email,
        password_hash=hash_password("secret"),
        plan="silver", country="UAE", currency="AED",
    )
    db.add(r); db.flush()
    db.add(AISetting(reseller_id=r.id))
    db.commit()
    rid = r.id
    yield r
    hard_delete_reseller(db, rid)


@pytest.fixture()
def admin_user(db: Session) -> AdminUser:
    """A dedicated admin user for tests. We mint a token directly via
    issue_jwt instead of logging in, because the /auth/login admin path
    is reserved for the protected admin email."""
    email = f"test-admin+{secrets.token_hex(4)}@example.test"
    a = AdminUser(
        name="Test Admin",
        email=email,
        password_hash=hash_password("secret"),
        level="Admin",
        enabled=True,
    )
    db.add(a); db.commit()
    yield a
    db.delete(a); db.commit()


@pytest.fixture()
def token(client: TestClient, reseller: Reseller) -> str:
    r = client.post("/auth/login", json={"email": reseller.email, "password": "secret"})
    return r.json()["access_token"]


@pytest.fixture()
def admin_token(admin_user: AdminUser) -> str:
    return issue_jwt(admin_user.id, kind="admin", role="admin")


@pytest.fixture()
def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin_auth(admin_token: str) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture()
def pool_uae(db: Session) -> PoolNumber:
    existing = db.query(PoolNumber).filter(
        PoolNumber.country_code == "UAE", PoolNumber.status == "active"
    ).first()
    if existing:
        return existing
    n = PoolNumber(
        number=f"+971 4 555 {secrets.randbelow(9000) + 1000}",
        country="United Arab Emirates", country_code="UAE", flag="🇦🇪",
        capacity=50, assigned=0, status="active",
    )
    db.add(n); db.commit()
    return n
