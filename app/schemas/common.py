from typing import Optional, List, Any
from datetime import datetime
from pydantic import BaseModel, EmailStr, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    reseller: "ResellerOut"


class SignupIn(BaseModel):
    name: str
    email: EmailStr
    password: str
    country: str = "UAE"
    currency: str = "AED"


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class ResellerOut(ORMModel):
    id: str
    name: str
    email: EmailStr
    plan: str
    status: str
    country: str
    currency: str
    role: str
    created_at: datetime


TokenResponse.model_rebuild()


class AISettingsOut(ORMModel):
    ai_name: str
    role: str
    tone: str
    creativity: int
    response_length: str
    primary_language: str
    supported_languages: List[str]
    always_sound_human: bool
    upsell_aggressiveness: int
    convince_hesitant: bool
    fallback_to_human: bool
    business_hours: Optional[List[Any]] = []


class AISettingsUpdate(BaseModel):
    ai_name: Optional[str] = None
    role: Optional[str] = None
    tone: Optional[str] = None
    creativity: Optional[int] = None
    response_length: Optional[str] = None
    primary_language: Optional[str] = None
    supported_languages: Optional[List[str]] = None
    always_sound_human: Optional[bool] = None
    upsell_aggressiveness: Optional[int] = None
    convince_hesitant: Optional[bool] = None
    fallback_to_human: Optional[bool] = None
    business_hours: Optional[List[Any]] = None


class MetaConfigIn(BaseModel):
    pixel_id: Optional[str] = None
    capi_access_token: Optional[str] = None
    test_event_code: Optional[str] = None
    default_event: Optional[str] = None       # InitiateCheckout | AddToCart | ViewContent | Lead
    action_source: Optional[str] = None       # website | business_messaging


class MetaConfigOut(BaseModel):
    pixel_id: Optional[str]
    has_token: bool
    test_event_code: Optional[str]
    default_event: str = "InitiateCheckout"
    action_source: str = "website"
    is_pixel_verified: bool = False
    is_capi_verified: bool = False
    verified: bool  # legacy aggregate


class WhatsAppConfigIn(BaseModel):
    number_type: str  # own | universal
    waba_id: Optional[str] = None
    phone_number_id: Optional[str] = None
    display_phone_number: Optional[str] = None
    access_token: Optional[str] = None
    webhook_verify_token: Optional[str] = None


class WhatsAppConfigOut(BaseModel):
    number_type: str
    waba_id: Optional[str]
    phone_number_id: Optional[str]
    display_phone_number: Optional[str]
    has_token: bool
    verified: bool
