"""Pricing engine: resolves the line + order total for given items.

Precedence per line:
  variant.price (override) → product.price (base)
  → if total qty of this product matches a bundle tier, use bundle price
  → then apply product.discount (percent or flat) to the resulting unit/line price
"""
from dataclasses import dataclass
from typing import Iterable, List, Optional

from ..models import Product, ProductVariant, ProductBundle


@dataclass
class LineRequest:
    product: Product
    variant: Optional[ProductVariant]
    qty: int


@dataclass
class LineQuote:
    product_id: str
    variant_id: Optional[str]
    qty: int
    unit_price: float
    line_total: float
    note: Optional[str] = None


@dataclass
class OrderQuote:
    lines: List[LineQuote]
    subtotal: float
    currency: str


def _best_bundle(bundles: List[ProductBundle], qty: int) -> Optional[ProductBundle]:
    """Return the bundle with the largest qty <= requested qty."""
    eligible = [b for b in bundles if b.qty <= qty]
    if not eligible:
        return None
    return max(eligible, key=lambda b: b.qty)


def _apply_discount(amount: float, dtype: Optional[str], dvalue: Optional[float]) -> float:
    if not dtype or dvalue is None or dvalue <= 0:
        return amount
    if dtype == "percent":
        return max(0.0, amount * (1 - min(100.0, dvalue) / 100.0))
    if dtype == "flat":
        return max(0.0, amount - dvalue)
    return amount


def quote_line(req: LineRequest) -> LineQuote:
    if req.qty <= 0:
        raise ValueError("qty must be positive")

    base_unit = req.variant.price if (req.variant and req.variant.price is not None) else req.product.price

    note = None
    bundle = _best_bundle(list(req.product.bundles or []), req.qty) if not req.variant else None
    # We only apply bundles when ordering the base product (no variant override),
    # because bundle pricing in the v2 spec is product-level not variant-level.
    if bundle is not None:
        # bundle.price is the total for `bundle.qty` units; pro-rate for the rest at base_unit
        bundled_units = bundle.qty
        leftover = req.qty - bundled_units
        line_total = bundle.price + (leftover * base_unit)
        unit_price = line_total / req.qty
        note = f"bundle {bundle.qty} for {bundle.price}"
    else:
        line_total = base_unit * req.qty
        unit_price = base_unit

    discounted_total = _apply_discount(
        line_total, req.product.discount_type, req.product.discount_value
    )
    if discounted_total != line_total:
        note = (note + " + " if note else "") + f"discount {req.product.discount_type} {req.product.discount_value}"
        line_total = discounted_total
        unit_price = line_total / req.qty

    return LineQuote(
        product_id=req.product.id,
        variant_id=req.variant.id if req.variant else None,
        qty=req.qty,
        unit_price=round(unit_price, 2),
        line_total=round(line_total, 2),
        note=note,
    )


def quote_order(lines: Iterable[LineRequest], currency: str = "AED") -> OrderQuote:
    quotes = [quote_line(l) for l in lines]
    subtotal = round(sum(q.line_total for q in quotes), 2)
    return OrderQuote(lines=quotes, subtotal=subtotal, currency=currency)
