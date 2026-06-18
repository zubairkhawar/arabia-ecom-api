from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..db import get_db
from ..deps import get_current_reseller, require_admin
from ..models import Reseller, Template
from ..schemas.templates import TemplateIn, TemplateUpdate, TemplateOut, TemplateApprove

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=List[TemplateOut])
def list_templates(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(Template).where(Template.reseller_id == current.id).order_by(Template.created_at.desc())
    ).scalars().all()
    return [TemplateOut.model_validate(r) for r in rows]


@router.post("", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(
    payload: TemplateIn,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    t = Template(
        reseller_id=current.id,
        name=payload.name,
        category=payload.category,
        language=payload.language,
        body=payload.body,
        status="pending",
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@router.patch("/{template_id}", response_model=TemplateOut)
def update_template(
    template_id: str,
    payload: TemplateUpdate,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    t = db.get(Template, template_id)
    if not t or t.reseller_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template not found")
    if t.status == "approved" and payload.body and payload.body != t.body:
        # Editing the body requires resubmission for approval
        t.status = "pending"
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(t, k, v)
    db.commit()
    db.refresh(t)
    return t


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(
    template_id: str,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    t = db.get(Template, template_id)
    if not t or t.reseller_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template not found")
    db.delete(t)
    db.commit()


# Admin override for the mock approval flow (Phase 1.5 wires this to the real Meta API)
@router.post("/{template_id}/approval", response_model=TemplateOut)
def set_approval(
    template_id: str,
    payload: TemplateApprove,
    _: Reseller = Depends(require_admin),
    db: Session = Depends(get_db),
):
    t = db.get(Template, template_id)
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template not found")
    if payload.status not in ("approved", "rejected", "pending"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "status must be approved|rejected|pending")
    t.status = payload.status
    if payload.status == "approved":
        t.meta_template_name = t.name.lower().replace(" ", "_")[:120]
        t.rejection_reason = None
    elif payload.status == "rejected":
        t.rejection_reason = payload.rejection_reason
    db.commit()
    db.refresh(t)
    return t
