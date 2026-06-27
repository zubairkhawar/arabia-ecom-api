from typing import Optional, List
from sqlalchemy import String, Integer, Float, ForeignKey, JSON, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ._base import IdMixin, TimestampMixin


class Product(Base, IdMixin, TimestampMixin):
    __tablename__ = "products"

    reseller_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resellers.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(String(500))
    description: Mapped[Optional[str]] = mapped_column(Text)  # short tagline
    main_description: Mapped[Optional[str]] = mapped_column(Text)  # long
    key_points: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="AED")
    country: Mapped[str] = mapped_column(String(8), default="UAE")
    channels: Mapped[list] = mapped_column(JSON, default=lambda: ["whatsapp"])
    source: Mapped[str] = mapped_column(String(64), default="manual")
    shopify_store_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("shopify_stores.id", ondelete="SET NULL"), index=True
    )
    shopify_product_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    discount_type: Mapped[Optional[str]] = mapped_column(String(16))  # percent | flat | None
    discount_value: Mapped[Optional[float]] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    options: Mapped[List["ProductOption"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    variants: Mapped[List["ProductVariant"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    bundles: Mapped[List["ProductBundle"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductOption(Base, IdMixin):
    __tablename__ = "product_options"

    product_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "Size"
    values: Mapped[list] = mapped_column(JSON, default=list)        # e.g. ["S","M","L"]
    position: Mapped[int] = mapped_column(Integer, default=0)

    product: Mapped["Product"] = relationship(back_populates="options")


class ProductVariant(Base, IdMixin):
    __tablename__ = "product_variants"

    product_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)  # "M / Red"
    combo: Mapped[dict] = mapped_column(JSON, default=dict)          # {Size:"M", Color:"Red"}
    price: Mapped[Optional[float]] = mapped_column(Float)            # override base
    stock: Mapped[Optional[int]] = mapped_column(Integer)
    sku: Mapped[Optional[str]] = mapped_column(String(64))

    product: Mapped["Product"] = relationship(back_populates="variants")


class ProductBundle(Base, IdMixin):
    __tablename__ = "product_bundles"

    product_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)

    product: Mapped["Product"] = relationship(back_populates="bundles")
