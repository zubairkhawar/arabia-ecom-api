from app.services.pricing import LineRequest, quote_line, quote_order
from app.models import Product, ProductVariant, ProductBundle


def _p(price=200, dt=None, dv=None, bundles=()):
    p = Product(
        id="pid", reseller_id="rid", name="X", slug="x", price=price, currency="AED",
        country="UAE", channels=["whatsapp"], source="manual", active=True,
        discount_type=dt, discount_value=dv,
    )
    p.bundles = [ProductBundle(qty=b[0], price=b[1]) for b in bundles]
    p.variants = []
    return p


def test_simple_line():
    q = quote_line(LineRequest(product=_p(100), variant=None, qty=2))
    assert q.unit_price == 100
    assert q.line_total == 200


def test_percent_discount():
    q = quote_line(LineRequest(product=_p(100, "percent", 10), variant=None, qty=1))
    assert q.line_total == 90
    assert q.unit_price == 90


def test_flat_discount_floored_at_zero():
    q = quote_line(LineRequest(product=_p(50, "flat", 200), variant=None, qty=1))
    assert q.line_total == 0


def test_bundle_match():
    # 2-for-350 → 175 each
    q = quote_line(LineRequest(product=_p(200, bundles=[(2, 350)]), variant=None, qty=2))
    assert q.line_total == 350
    assert q.unit_price == 175


def test_bundle_with_leftover():
    # Bundle 2-for-350, ordering 3 → 350 + 1*200 = 550
    q = quote_line(LineRequest(product=_p(200, bundles=[(2, 350)]), variant=None, qty=3))
    assert q.line_total == 550


def test_best_bundle_selected():
    p = _p(200, bundles=[(2, 350), (3, 480)])
    # qty=3 should pick the 3-for-480 bundle, not the 2-for-350
    q = quote_line(LineRequest(product=p, variant=None, qty=3))
    assert q.line_total == 480


def test_bundle_plus_percent_discount():
    # 2-for-350 then -10% = 315
    p = _p(200, dt="percent", dv=10, bundles=[(2, 350)])
    q = quote_line(LineRequest(product=p, variant=None, qty=2))
    assert q.line_total == 315


def test_variant_override_overrides_base():
    p = _p(200)
    v = ProductVariant(id="vid", product_id="pid", label="L", combo={}, price=275, stock=10, sku=None)
    p.variants = [v]
    q = quote_line(LineRequest(product=p, variant=v, qty=2))
    assert q.line_total == 550


def test_variant_skips_bundle_pricing():
    """Variants are an override at the SKU level; bundles are product-level.
    When a variant override is set, we use it raw (no bundle resolution)."""
    p = _p(200, bundles=[(2, 350)])
    v = ProductVariant(id="vid", product_id="pid", label="Premium", combo={}, price=220, stock=None, sku=None)
    p.variants = [v]
    q = quote_line(LineRequest(product=p, variant=v, qty=2))
    assert q.line_total == 440  # 220 * 2, NOT 350


def test_order_total_sums_lines():
    p1 = _p(100)
    p1.id = "p1"
    p2 = _p(50)
    p2.id = "p2"
    q = quote_order([
        LineRequest(product=p1, variant=None, qty=2),
        LineRequest(product=p2, variant=None, qty=3),
    ])
    assert q.subtotal == 350
    assert q.lines[0].line_total == 200
    assert q.lines[1].line_total == 150
