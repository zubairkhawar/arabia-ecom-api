from typing import Optional
from pydantic import BaseModel
from datetime import datetime
from .common import ORMModel


class TemplateIn(BaseModel):
    name: str
    category: str = "custom"
    language: str = "en"
    body: str


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    language: Optional[str] = None
    body: Optional[str] = None


class TemplateOut(ORMModel):
    id: str
    name: str
    category: str
    language: str
    body: str
    status: str
    meta_template_name: Optional[str]
    rejection_reason: Optional[str]
    created_at: datetime
    updated_at: datetime


class TemplateApprove(BaseModel):
    status: str  # approved | rejected | pending
    rejection_reason: Optional[str] = None
