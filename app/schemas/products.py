from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from datetime import datetime
from .common import ORMModel


class OptionIn(BaseModel):
    name: str
    values: List[str]


class VariantIn(BaseModel):
    label: str
    combo: Dict[str, str] = {}
    price: Optional[float] = None
    stock: Optional[int] = None
    sku: Optional[str] = None


class BundleIn(BaseModel):
    qty: int
    price: float


class DiscountIn(BaseModel):
    type: str  # percent | flat
    value: float


class ProductIn(BaseModel):
    name: str
    image_url: Optional[str] = None
    description: Optional[str] = None
    main_description: Optional[str] = None
    key_points: Optional[List[str]] = []
    price: float
    currency: str = "AED"
    country: str = "UAE"
    channels: List[str] = ["whatsapp"]
    discount: Optional[DiscountIn] = None
    bundles: Optional[List[BundleIn]] = []
    options: Optional[List[OptionIn]] = []
    variants: Optional[List[VariantIn]] = []


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    image_url: Optional[str] = None
    description: Optional[str] = None
    main_description: Optional[str] = None
    key_points: Optional[List[str]] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    country: Optional[str] = None
    channels: Optional[List[str]] = None
    discount: Optional[DiscountIn] = None
    bundles: Optional[List[BundleIn]] = None
    options: Optional[List[OptionIn]] = None
    variants: Optional[List[VariantIn]] = None
    active: Optional[bool] = None


class OptionOut(ORMModel):
    name: str
    values: List[str]
    position: int


class VariantOut(ORMModel):
    id: str
    label: str
    combo: Dict[str, Any]
    price: Optional[float]
    stock: Optional[int]
    sku: Optional[str]


class BundleOut(ORMModel):
    qty: int
    price: float


class ProductOut(ORMModel):
    id: str
    name: str
    slug: str
    image_url: Optional[str]
    description: Optional[str]
    main_description: Optional[str]
    key_points: Optional[List[str]]
    price: float
    currency: str
    country: str
    channels: List[str]
    discount_type: Optional[str]
    discount_value: Optional[float]
    active: bool
    source: str
    options: List[OptionOut] = []
    variants: List[VariantOut] = []
    bundles: List[BundleOut] = []
    generated_url: str
    created_at: datetime


class QuoteLineIn(BaseModel):
    product_id: str
    variant_id: Optional[str] = None
    qty: int


class QuoteIn(BaseModel):
    lines: List[QuoteLineIn]


class QuoteLineOut(BaseModel):
    product_id: str
    variant_id: Optional[str]
    qty: int
    unit_price: float
    line_total: float
    note: Optional[str]


class QuoteOut(BaseModel):
    lines: List[QuoteLineOut]
    subtotal: float
    currency: str
