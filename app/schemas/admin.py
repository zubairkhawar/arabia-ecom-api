from typing import Optional, List
from pydantic import BaseModel, EmailStr
from datetime import datetime
from .common import ORMModel


class PoolNumberIn(BaseModel):
    number: str
    country: str
    country_code: str
    flag: str = "🌍"
    capacity: int = 50
    waba_id: Optional[str] = None
    phone_number_id: Optional[str] = None
    access_token: Optional[str] = None


class PoolNumberUpdate(BaseModel):
    status: Optional[str] = None  # active|disabled
    capacity: Optional[int] = None


class PoolNumberOut(ORMModel):
    id: str
    number: str
    country: str
    country_code: str
    flag: str
    capacity: int
    assigned: int
    status: str
    has_token: bool = False


class PoolAssignmentOut(BaseModel):
    reseller_id: str
    reseller_name: str
    pool_number_id: str
    number: str
    country_code: str


class AdminUserIn(BaseModel):
    name: str
    email: EmailStr
    level: str = "Admin"


class AdminUserToggle(BaseModel):
    enabled: bool


class AdminUserOut(ORMModel):
    id: str
    name: str
    email: EmailStr
    level: str
    enabled: bool
    created_at: datetime


class ResellerSummary(ORMModel):
    id: str
    name: str
    email: EmailStr
    plan: str
    status: str
    country: str
    currency: str
    created_at: datetime
