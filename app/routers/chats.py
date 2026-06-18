from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select

from ..db import get_db
from ..deps import get_current_reseller
from ..models import Reseller, Chat, Customer, ClickSession, Message, WhatsAppConfig
from ..services.whatsapp_cloud import send_text
from ..schemas.chats import ChatSummary, ChatDetail, MessageOut, ModeChange, HumanReply

router = APIRouter(prefix="/chats", tags=["chats"])


@router.get("", response_model=List[ChatSummary])
def list_chats(
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(Chat).where(Chat.reseller_id == current.id).order_by(Chat.updated_at.desc())
    ).scalars().all()
    out = []
    for c in rows:
        last = c.messages[-1] if c.messages else None
        cust = db.get(Customer, c.customer_id)
        out.append(ChatSummary(
            id=c.id,
            customer_id=c.customer_id,
            customer_name=cust.name if cust else None,
            customer_phone=cust.phone if cust else "",
            channel=c.channel,
            mode=c.mode,
            unread=c.unread,
            last_message=last.text if last else None,
            last_message_at=last.created_at if last else None,
        ))
    return out


@router.get("/{chat_id}", response_model=ChatDetail)
def get_chat(
    chat_id: str,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    c = db.get(Chat, chat_id)
    if not c or c.reseller_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chat not found")
    c.unread = 0
    db.commit()
    cust = db.get(Customer, c.customer_id)
    cs = db.get(ClickSession, c.click_session_id) if c.click_session_id else None
    return ChatDetail(
        id=c.id,
        customer_id=c.customer_id,
        customer_name=cust.name if cust else None,
        customer_phone=cust.phone if cust else "",
        customer_location=cust.location if cust else None,
        customer_total_orders=cust.total_orders if cust else 0,
        customer_total_spent=cust.total_spent if cust else 0.0,
        channel=c.channel,
        mode=c.mode,
        click_session_id=c.click_session_id,
        src_platform=cs.src_platform if cs else None,
        draft_items=c.draft_items or [],
        messages=[MessageOut.model_validate({
            "id": m.id, "sender": m.sender, "text": m.text, "created_at": m.created_at
        }) for m in c.messages],
    )


@router.post("/{chat_id}/mode", response_model=ChatDetail)
def change_mode(
    chat_id: str,
    payload: ModeChange,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    c = db.get(Chat, chat_id)
    if not c or c.reseller_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chat not found")
    if payload.mode not in ("ai", "human"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "mode must be 'ai' or 'human'")
    c.mode = payload.mode
    db.commit()
    return get_chat(chat_id, current, db)


@router.post("/{chat_id}/reply")
async def human_reply(
    chat_id: str,
    payload: HumanReply,
    current: Reseller = Depends(get_current_reseller),
    db: Session = Depends(get_db),
):
    c = db.get(Chat, chat_id)
    if not c or c.reseller_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chat not found")
    if c.mode != "human":
        raise HTTPException(status.HTTP_409_CONFLICT, "Chat is not in human mode")
    cfg = db.execute(
        select(WhatsAppConfig).where(WhatsAppConfig.reseller_id == current.id)
    ).scalar_one_or_none()
    cust = db.get(Customer, c.customer_id)
    result = await send_text(
        cfg.phone_number_id if cfg else None,
        cfg.access_token_enc if cfg else None,
        cust.phone if cust else "",
        payload.text,
    )
    db.add(Message(chat_id=c.id, sender="human", text=payload.text))
    db.commit()
    return {"ok": True, "wa_result": result}
