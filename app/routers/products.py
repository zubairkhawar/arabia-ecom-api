from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select

from ..db import get_db
from ..deps import get_current_reseller
from ..models import Reseller, Product, ProductOption, ProductVariant, ProductBundle
from ..services.slug import short_slug
from ..services.pricing import quote_order, LineRequest
from ..config import settings
from ..schemas.products import (
    ProductIn,
    ProductUpdate,
    ProductOut,
    OptionOut,
    VariantOut,
    BundleOut,
    QuoteIn,
    QuoteOut,
    QuoteLineOut,
)

router = APIRouter(prefix="/products", tags=["products"])


def _serialize(p: Product) -> ProductOut:
    return ProductOut(
        id=p.id,
        name=p.name,
        slug=p.slug,
        image_url=p.image_url,
        description=p.description,
        main_description=p.main_description,
        key_points=p.key_points or [],
        price=p.price,
        currency=p.currency,
        country=p.country,
        channels=p.channels or [],
        discount_type=p.discount_type,
        discount_value=p.discount_value,
        active=p.active,
        source=p.source,
        options=[OptionOut.model_validate(o) for o in sorted(p.options, key=lambda x: x.position)],
        variants=[VariantOut.model_validate(v) for v in p.variants],
        bundles=[BundleOut.model_validate(b) for b in p.bundles],
        generated_url=f"{settings.link_domain}/r/{p.slug}",
        created_at=p.created_at,
    )


def _apply_options_variants(product: Product, payload):
    """Replace options/variants/bundles atomically."""
    # remove old
    for o in list(product.options):
        product.options.remove(o)
    for v in list(product.variants):
        product.variants.remove(v)
    for b in list(product.bundles):
        product.bundles.remove(b)

    for idx, opt in enumerate(payload.options or []):
        product.options.append(
            ProductOption(name=opt.name, values=opt.values, position=idx)
        )
    for v in payload.variants or []:
        product.variants.append(
            ProductVariant(
                label=v.label,
                combo=v.combo or {},
                price=v.price,
                stock=v.stock,
                sku=v.sku,
            )
        )
    for b in payload.bundles or []:
        product.bundles.append(ProductBundle(qty=b.qty, price=b.price))

    if payload.discount and payload.discount.value > 0:
        product.discount_type = payload.discount.type
        product.discount_value = payload.discount.value
    elif payload.discount is None and getattr(payload, "_clear_discount", False):
        product.discount_type = None
        product.discount_value = None


@router.get("", response_model=List[ProductOut])
def list_products(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
    q: Optional[str] = None,
    active: Optional[bool] = None,
):
    stmt = select(Product).where(Product.reseller_id == current.id).options(
        selectinload(Product.options),
        selectinload(Product.variants),
        selectinload(Product.bundles),
    )
    if active is not None:
        stmt = stmt.where(Product.active == active)
    if q:
        stmt = stmt.where(Product.name.ilike(f"%{q}%"))
    rows = db.execute(stmt.order_by(Product.created_at.desc())).scalars().all()
    return [_serialize(p) for p in rows]


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductIn,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    p = Product(
        reseller_id=current.id,
        name=payload.name,
        slug=short_slug(),
        image_url=payload.image_url,
        description=payload.description,
        main_description=payload.main_description,
        key_points=payload.key_points or [],
        price=payload.price,
        currency=payload.currency,
        country=payload.country,
        channels=payload.channels,
    )
    db.add(p)
    db.flush()
    _apply_options_variants(p, payload)
    db.commit()
    db.refresh(p)
    return _serialize(p)


@router.get("/{product_id}", response_model=ProductOut)
def get_product(
    product_id: str,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    p = db.get(Product, product_id)
    if not p or p.reseller_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return _serialize(p)


@router.patch("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: str,
    payload: ProductUpdate,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    p = db.get(Product, product_id)
    if not p or p.reseller_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    data = payload.model_dump(exclude_unset=True)
    for field in ("name", "image_url", "description", "main_description", "key_points",
                  "price", "currency", "country", "channels", "active"):
        if field in data:
            setattr(p, field, data[field])
    if "discount" in data:
        d = data["discount"]
        if d is None:
            p.discount_type = None
            p.discount_value = None
        else:
            p.discount_type = d["type"]
            p.discount_value = d["value"]
    if "options" in data or "variants" in data or "bundles" in data:
        if "options" in data:
            for o in list(p.options):
                p.options.remove(o)
            for idx, opt in enumerate(payload.options or []):
                p.options.append(ProductOption(name=opt.name, values=opt.values, position=idx))
        if "variants" in data:
            for v in list(p.variants):
                p.variants.remove(v)
            for v in payload.variants or []:
                p.variants.append(
                    ProductVariant(label=v.label, combo=v.combo or {}, price=v.price, stock=v.stock, sku=v.sku)
                )
        if "bundles" in data:
            for b in list(p.bundles):
                p.bundles.remove(b)
            for b in payload.bundles or []:
                p.bundles.append(ProductBundle(qty=b.qty, price=b.price))
    db.commit()
    db.refresh(p)
    return _serialize(p)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: str,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    p = db.get(Product, product_id)
    if not p or p.reseller_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    db.delete(p)
    db.commit()


@router.post("/quote", response_model=QuoteOut)
def quote(
    payload: QuoteIn,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    """Compute pricing for a set of line items (variant + qty), applying
    variant overrides, bundle tiers, and product discount in that order."""
    line_reqs = []
    for l in payload.lines:
        p = db.get(Product, l.product_id)
        if not p or p.reseller_id != current.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Product {l.product_id} not found")
        v = None
        if l.variant_id:
            v = next((x for x in p.variants if x.id == l.variant_id), None)
            if not v:
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"Variant {l.variant_id} not found")
        line_reqs.append(LineRequest(product=p, variant=v, qty=l.qty))
    q = quote_order(line_reqs, currency=current.currency)
    return QuoteOut(
        lines=[QuoteLineOut(**vars(l)) for l in q.lines],
        subtotal=q.subtotal,
        currency=q.currency,
    )
