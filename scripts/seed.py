"""Seed the database with starter data.

Idempotent: re-running is safe (uses email uniqueness for resellers / number
uniqueness for pool numbers / plan code uniqueness etc.)
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from app.db import SessionLocal
from app.models import (
    Reseller, AISetting, Plan, PoolNumber, AdminUser, Product, Template,
    ProductOption, ProductVariant, ProductBundle, MetaConfig, WhatsAppConfig,
)
from app.security import hash_password


def upsert_plans(db):
    plans = [
        {"code": "silver", "name": "Silver", "price": 99, "orders_cap": 500, "conversations_cap": 2000, "stores_cap": 1, "universal_numbers_cap": 1, "features": ["WhatsApp inbox", "Basic AI", "1 store", "1 universal number"]},
        {"code": "gold", "name": "Gold", "price": 299, "orders_cap": 5000, "conversations_cap": 20000, "stores_cap": 3, "universal_numbers_cap": 3, "features": ["Everything in Silver", "Templates", "CSV import/export", "3 stores", "3 universal numbers"]},
        {"code": "platinum", "name": "Platinum", "price": 749, "orders_cap": None, "conversations_cap": None, "stores_cap": 10, "universal_numbers_cap": 10, "features": ["Everything in Gold", "Unlimited conversations", "Unlimited orders", "Priority support"]},
    ]
    for p in plans:
        existing = db.execute(select(Plan).where(Plan.code == p["code"])).scalar_one_or_none()
        if not existing:
            db.add(Plan(currency="AED", **p))
            print(f"  + plan {p['code']}")
    db.commit()


def upsert_pool_numbers(db):
    nums = [
        ("+971 4 555 0101", "United Arab Emirates", "UAE", "🇦🇪", 50, 0),
        ("+971 4 555 0102", "United Arab Emirates", "UAE", "🇦🇪", 50, 0),
        ("+92 21 555 0201", "Pakistan", "PAK", "🇵🇰", 50, 0),
        ("+966 11 555 0301", "Saudi Arabia", "KSA", "🇸🇦", 50, 0),
    ]
    for number, country, code, flag, cap, assigned in nums:
        existing = db.execute(select(PoolNumber).where(PoolNumber.number == number)).scalar_one_or_none()
        if not existing:
            db.add(PoolNumber(number=number, country=country, country_code=code, flag=flag, capacity=cap, assigned=assigned, status="active"))
            print(f"  + pool {number}")
    db.commit()


def upsert_admin(db):
    email = "safdar@arabia-ai.com"
    existing = db.execute(select(AdminUser).where(AdminUser.email == email)).scalar_one_or_none()
    if not existing:
        db.add(AdminUser(name="Safdar Khan", email=email, password_hash=hash_password("change-me-now"), level="Owner", enabled=True))
        print(f"  + admin {email}")
    db.commit()


def upsert_demo_reseller(db):
    email = "demo@arabia-ai.com"
    existing = db.execute(select(Reseller).where(Reseller.email == email)).scalar_one_or_none()
    if existing:
        return existing
    r = Reseller(
        name="Aurora Store (demo)", email=email,
        password_hash=hash_password("demo123!"),
        plan="gold", country="UAE", currency="AED", role="reseller",
    )
    db.add(r); db.flush()
    db.add(AISetting(reseller_id=r.id))
    db.add(MetaConfig(reseller_id=r.id))
    db.add(WhatsAppConfig(reseller_id=r.id, number_type="universal", verified=True))
    print(f"  + reseller {email} (password: demo123!)")

    # 3 demo products
    products_data = [
        {
            "name": "Wireless Earbuds Pro", "price": 199, "country": "UAE",
            "description": "ANC, 24h battery",
            "main_description": "Active noise cancellation, multipoint Bluetooth, 24h with case.",
            "key_points": ["ANC", "24h battery", "Free shipping UAE"],
            "discount": ("percent", 10),
            "bundles": [(2, 350), (3, 480)],
            "options": [("Color", ["Black", "White"])],
            "variants": [("Black", {"Color": "Black"}, None), ("White", {"Color": "White"}, 215)],
        },
        {
            "name": "Smart Fitness Watch", "price": 349, "country": "UAE",
            "description": "Heart rate, SpO2, GPS",
            "key_points": ["GPS", "14-day standby", "Heart rate"],
            "discount": None, "bundles": [], "options": [], "variants": [],
        },
        {
            "name": "Mini Projector HD", "price": 599, "country": "UAE",
            "description": "1080p, Wi-Fi",
            "key_points": ["1080p native", "Wi-Fi", "HDMI/USB"],
            "discount": None, "bundles": [(2, 1100)], "options": [], "variants": [],
        },
    ]
    from app.services.slug import short_slug
    for pd in products_data:
        p = Product(
            reseller_id=r.id, name=pd["name"], slug=short_slug(),
            price=pd["price"], currency="AED", country=pd["country"],
            description=pd.get("description"),
            main_description=pd.get("main_description"),
            key_points=pd.get("key_points", []),
            channels=["whatsapp"], source="manual",
        )
        if pd.get("discount"):
            p.discount_type, p.discount_value = pd["discount"]
        db.add(p); db.flush()
        for idx, (oname, ovals) in enumerate(pd.get("options", [])):
            db.add(ProductOption(product_id=p.id, name=oname, values=ovals, position=idx))
        for label, combo, price in pd.get("variants", []):
            db.add(ProductVariant(product_id=p.id, label=label, combo=combo, price=price))
        for qty, price in pd.get("bundles", []):
            db.add(ProductBundle(product_id=p.id, qty=qty, price=price))
        print(f"    · product {p.name} → slug {p.slug}")

    # 2 templates: 1 approved, 1 pending
    db.add(Template(reseller_id=r.id, name="Delivered review", category="delivered", language="en",
                    body="Hi {customer_name}, your order {order_id} was delivered. Would you leave us a review?",
                    status="approved", meta_template_name="delivered_review"))
    db.add(Template(reseller_id=r.id, name="Return follow-up", category="return", language="en",
                    body="Hi {customer_name}, we received your return for order {order_id}.",
                    status="approved", meta_template_name="return_followup"))
    db.add(Template(reseller_id=r.id, name="Custom offer", category="offer", language="en",
                    body="{customer_name}, here's a special offer just for you 🎁",
                    status="pending"))
    db.commit()
    return r


if __name__ == "__main__":
    db = SessionLocal()
    try:
        print("Seeding plans...")
        upsert_plans(db)
        print("Seeding pool numbers...")
        upsert_pool_numbers(db)
        print("Seeding admin user...")
        upsert_admin(db)
        print("Seeding demo reseller...")
        upsert_demo_reseller(db)
        print("\n✓ Seed complete.")
        print("\nDemo login:")
        print("  email:    demo@arabia-ai.com")
        print("  password: demo123!")
    finally:
        db.close()
