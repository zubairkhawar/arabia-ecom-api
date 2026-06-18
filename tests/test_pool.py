"""Pool spillover: when a number fills, the next active number takes new resellers."""
from app.services.pool_router import get_or_assign
from app.models import PoolNumber, PoolAssignment, Reseller
from app.security import hash_password
import secrets


def _r(name="x"):
    return Reseller(
        name=name,
        email=f"pool+{secrets.token_hex(4)}@example.com",
        password_hash=hash_password("x"),
        country="EGY",
        currency="EGP",
    )


def test_assignment_creates_pool_assignment(db):
    n = PoolNumber(number=f"+20-{secrets.randbelow(99999)}", country="Egypt",
                   country_code="EGY", flag="🇪🇬", capacity=2, assigned=0, status="active")
    db.add(n); db.flush()
    r = _r(); db.add(r); db.flush()
    got = get_or_assign(db, r)
    assert got is not None
    assert got.id == n.id
    assert got.assigned == 1
    db.rollback()


def test_spillover_to_next_number(db):
    n1 = PoolNumber(number=f"+20-A-{secrets.randbelow(99999)}", country="Egypt",
                    country_code="EGY", flag="🇪🇬", capacity=1, assigned=0, status="active")
    n2 = PoolNumber(number=f"+20-B-{secrets.randbelow(99999)}", country="Egypt",
                    country_code="EGY", flag="🇪🇬", capacity=1, assigned=0, status="active")
    db.add_all([n1, n2]); db.flush()

    r1 = _r("a"); db.add(r1); db.flush()
    r2 = _r("b"); db.add(r2); db.flush()

    g1 = get_or_assign(db, r1)
    assert g1.assigned == 1
    assert g1.status == "full"

    g2 = get_or_assign(db, r2)
    assert g2.id != g1.id  # spilled to the second number
    assert g2.assigned == 1
    db.rollback()


def test_returns_existing_assignment(db):
    n = PoolNumber(number=f"+20-{secrets.randbelow(99999)}", country="Egypt",
                   country_code="EGY", flag="🇪🇬", capacity=5, assigned=0, status="active")
    db.add(n); db.flush()
    r = _r(); db.add(r); db.flush()
    first = get_or_assign(db, r)
    second = get_or_assign(db, r)
    assert first.id == second.id
    # only one assignment created
    count = db.query(PoolAssignment).filter(PoolAssignment.reseller_id == r.id).count()
    assert count == 1
    db.rollback()
