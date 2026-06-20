from typing import Optional, List
from pydantic import BaseModel


class LinkResolveOut(BaseModel):
    product_id: str
    product_name: str
    product_image: Optional[str]
    price: float
    currency: str
    pixel_id: Optional[str]
    default_event: str = "InitiateCheckout"
    wa_target_number: str
    wa_deeplink: str           # e.g. https://wa.me/971...?text=...%20[ref:c_xxx]
    ref_token: str
    reseller_id: str
    reseller_name: str


class ClickIn(BaseModel):
    slug: str
    src_platform: Optional[str] = "other"
    fbclid: Optional[str] = None
    fbp: Optional[str] = None
    fbc: Optional[str] = None  # pre-built by client if it has one
    ttclid: Optional[str] = None
    sclid: Optional[str] = None
    gclid: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_content: Optional[str] = None
    utm_term: Optional[str] = None
    user_agent: Optional[str] = None
    referer: Optional[str] = None
    landing_url: Optional[str] = None


class ClickOut(BaseModel):
    ref_token: str
    click_session_id: str
    event_id: str
    pixel_id: Optional[str]
    wa_deeplink: str
    capi_status: str
    capi_response_code: Optional[int]
    bot: bool = False


class PixelFiredIn(BaseModel):
    click_session_id: str


class MetaVerifyOut(BaseModel):
    ok: bool
    capi_status: int
    capi_response: str
    pixel_id: Optional[str]
    verified: bool


class AttributionEventOut(BaseModel):
    id: str
    platform: str
    event_name: str
    event_id: str
    value: Optional[float]
    currency: Optional[str]
    status: str
    response_code: Optional[int]
    response_body: Optional[str]
    created_at: str
