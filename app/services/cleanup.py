"""FK-safe deletion helpers.

`hard_delete_reseller` exists so the admin-bootstrap can remove a reseller
that occupies the protected admin email, AND so tests can clean up after
themselves on the shared Render dev DB. Production order_items have a
RESTRICT FK to products by design — this helper bypasses it intentionally
for full-reseller wipe operations.
"""
from sqlalchemy import text
from sqlalchemy.orm import Session


_STATEMENTS = (
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
)


def hard_delete_reseller(db: Session, reseller_id: str) -> None:
    p = {"rid": reseller_id}
    for s in _STATEMENTS:
        db.execute(text(s), p)
    db.commit()
