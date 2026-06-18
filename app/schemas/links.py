from typing import Optional, List
from pydantic import BaseModel


class LinkResolveOut(BaseModel):
    product_id: str
    product_name: str
    product_image: Optional[str]
    price: float
    currency: str
    pixel_id: Optional[str]
    wa_target_number: str
    wa_deeplink: str           # e.g. https://wa.me/971...?text=...%20[c_xxx]
    ref_token: str
    reseller_id: str
    reseller_name: str


class ClickIn(BaseModel):
    slug: str
    src_platform: Optional[str] = "other"
    fbclid: Optional[str] = None
    fbp: Optional[str] = None
    ttclid: Optional[str] = None
    sclid: Optional[str] = None
    gclid: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    user_agent: Optional[str] = None
    referer: Optional[str] = None


class ClickOut(BaseModel):
    ref_token: str
    click_session_id: str
    event_id: str
    pixel_id: Optional[str]
    wa_deeplink: str
    capi_status: str
    capi_response_code: Optional[int]


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
