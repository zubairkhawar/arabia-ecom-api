"""Test fixtures.

The Render DB is shared (dev), so each fixture cleans up after itself in
FK-safe order. `_hard_delete_reseller` is a TEST-ONLY helper — production
keeps RESTRICT on order_items.product_id so deleting an individual product
doesn't wipe order history.
"""
import pytest
import secrets
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app
from app.models import Reseller, AISetting, PoolNumber
from app.security import hash_password


def _hard_delete_reseller(db: Session, reseller_id: str) -> None:
    """Delete a reseller and everything they touched, in FK-safe order."""
    p = {"rid": reseller_id}
    statements = [
        "DELETE FROM attribution_events WHERE reseller_id = :rid",
        "DELETE FROM order_items WHERE order_id IN (SELECT id FROM orders WHERE reseller_id = :rid)",
        "DELETE FROM orders WHERE reseller_id = :rid",
        "DELETE FROM messages WHERE chat_id IN (SELECT id FROM chats WHERE reseller_id = :rid)",
        "DELETE FROM chats WHERE reseller_id = :rid",
        "DELETE FROM click_sessions WHERE reseller_id = :rid",
        "DELETE FROM product_options WHERE product_id IN (SELECT id FROM products WHERE reseller_id = :rid)",
        "DELETE FROM product_variants WHERE product_id IN (SELECT id FROM products WHERE reseller_id = :rid)",
        "DELETE FROM product_bundles WHERE product_id IN (SELECT id FROM products WHERE reseller_id = :rid)",
        "DELETE FROM products WHERE reseller_id = :rid",
        "DELETE FROM customers WHERE reseller_id = :rid",
        "DELETE FROM templates WHERE reseller_id = :rid",
        "DELETE FROM ai_settings WHERE reseller_id = :rid",
        "DELETE FROM meta_configs WHERE reseller_id = :rid",
        "DELETE FROM whatsapp_configs WHERE reseller_id = :rid",
        "DELETE FROM pool_assignments WHERE reseller_id = :rid",
        "DELETE FROM usages WHERE reseller_id = :rid",
        "DELETE FROM resellers WHERE id = :rid",
    ]
    for s in statements:
        db.execute(text(s), p)
    db.commit()


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
    _hard_delete_reseller(db, rid)


@pytest.fixture()
def admin_user(db: Session) -> Reseller:
    email = f"admin+{secrets.token_hex(4)}@example.com"
    r = Reseller(
        name="Admin", email=email, password_hash=hash_password("secret"),
        plan="platinum", role="admin", country="UAE", currency="AED",
    )
    db.add(r); db.commit()
    rid = r.id
    yield r
    _hard_delete_reseller(db, rid)


@pytest.fixture()
def token(client: TestClient, reseller: Reseller) -> str:
    r = client.post("/auth/login", json={"email": reseller.email, "password": "secret"})
    return r.json()["access_token"]


@pytest.fixture()
def admin_token(client: TestClient, admin_user: Reseller) -> str:
    r = client.post("/auth/login", json={"email": admin_user.email, "password": "secret"})
    return r.json()["access_token"]


@pytest.fixture()
def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin_auth(admin_token: str) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture()
def pool_uae(db: Session) -> PoolNumber:
    """Ensure there's at least one active UAE pool number. Does not delete it
    at the end — pool numbers are admin-managed shared infra in this DB."""
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
