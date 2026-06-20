"""WhatsApp Cloud API inbound webhook.

URL: /webhooks/wa/{reseller_id}
GET: Meta hub verification challenge
POST: inbound message payload

On inbound:
1. Find the chat for this customer (or create one).
2. If the message contains a [c_xxxxxxxx] ref_token, match it to a ClickSession.
3. Persist customer message, ask the AI for a reply.
4. Parse INTENT block from AI reply; execute (add item / confirm order).
5. Send the cleaned reply back via WA Cloud API.
6. Persist the assistant message.
"""
import re
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..db import get_db
from ..config import settings
from ..models import (
    Reseller, Chat, Message, ClickSession, Product, Customer, AISetting,
    WhatsAppConfig,
)
from ..services.ai import build_system_prompt, chat_complete, parse_intent
from ..services.orders import find_or_create_customer, create_order_from_items, confirm_order
from ..services.whatsapp_cloud import send_text

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Matches both the spec format [ref:c_xxx] and the legacy [c_xxx] so older
# pre-format-change deeplinks still attribute correctly.
REF_RE = re.compile(r"\[(?:ref:)?(c_[a-z0-9]{4,12})\]")


@router.get("/wa/{reseller_id}")
def wa_verify(
    reseller_id: str,
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
    db: Session = Depends(get_db),
):
    """Meta webhook verification handshake."""
    if hub_mode != "subscribe":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "expected mode=subscribe")
    expected = settings.wa_verify_token
    cfg = db.execute(
        select(WhatsAppConfig).where(WhatsAppConfig.reseller_id == reseller_id)
    ).scalar_one_or_none()
    if cfg and cfg.webhook_verify_token:
        expected = cfg.webhook_verify_token
    if hub_verify_token != expected:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "bad verify token")
    return Response(content=hub_challenge, media_type="text/plain")


@router.post("/wa/{reseller_id}")
async def wa_inbound(reseller_id: str, request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    reseller = db.get(Reseller, reseller_id)
    if not reseller:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reseller not found")

    # Parse Meta WA Cloud API payload shape
    msgs_to_process = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for m in value.get("messages", []) or []:
                if m.get("type") != "text":
                    continue
                msgs_to_process.append({
                    "wa_message_id": m.get("id"),
                    "from": m.get("from"),
                    "text": m.get("text", {}).get("body", ""),
                    "profile_name": (value.get("contacts", [{}])[0]
                                     .get("profile", {}).get("name") if value.get("contacts") else None),
                })

    if not msgs_to_process:
        return {"ok": True, "processed": 0}

    wa_cfg = db.execute(
        select(WhatsAppConfig).where(WhatsAppConfig.reseller_id == reseller.id)
    ).scalar_one_or_none()

    processed = 0
    for inbound in msgs_to_process:
        await _handle_inbound_message(db, reseller, wa_cfg, inbound)
        processed += 1
    db.commit()
    return {"ok": True, "processed": processed}


async def _handle_inbound_message(
    db: Session,
    reseller: Reseller,
    wa_cfg: Optional[WhatsAppConfig],
    inbound: dict,
):
    phone = inbound["from"]
    text = inbound["text"] or ""

    # Attribution match via ref_token
    click_session_id: Optional[str] = None
    ref_match = REF_RE.search(text)
    if ref_match:
        cs = db.execute(
            select(ClickSession).where(ClickSession.ref_token == ref_match.group(1))
        ).scalar_one_or_none()
        if cs and cs.reseller_id == reseller.id:
            click_session_id = cs.id

    # Find/create customer + chat
    customer = find_or_create_customer(db, reseller, phone, name=inbound.get("profile_name"))
    chat = db.execute(
        select(Chat).where(
            Chat.reseller_id == reseller.id, Chat.wa_thread_id == phone
        )
    ).scalar_one_or_none()
    if not chat:
        chat = Chat(
            reseller_id=reseller.id,
            customer_id=customer.id,
            channel="whatsapp",
            mode="ai",
            unread=0,
            wa_thread_id=phone,
            click_session_id=click_session_id,
        )
        db.add(chat)
        db.flush()
        if click_session_id:
            cs = db.get(ClickSession, click_session_id)
            if cs:
                cs.matched_chat_id = chat.id
    elif click_session_id and not chat.click_session_id:
        chat.click_session_id = click_session_id

    # Persist inbound message
    db.add(Message(chat_id=chat.id, sender="customer", text=text, wa_message_id=inbound.get("wa_message_id")))
    chat.unread = (chat.unread or 0) + 1
    db.flush()

    # If chat is in human mode, do not auto-reply
    if chat.mode == "human":
        return

    # Build AI history (skip the just-added user message; pass it as user_message)
    history = []
    for m in chat.messages[:-1]:
        role = "user" if m.sender == "customer" else "assistant"
        history.append({"role": role, "content": m.text})

    # Catalogue context
    catalogue = db.execute(
        select(Product).where(Product.reseller_id == reseller.id, Product.active == True).limit(50)
    ).scalars().all()
    ai_settings = db.execute(
        select(AISetting).where(AISetting.reseller_id == reseller.id)
    ).scalar_one_or_none() or AISetting(reseller_id=reseller.id)

    prompt = build_system_prompt(reseller, ai_settings, catalogue)
    raw_reply = await chat_complete(prompt, history, text)
    clean, intent = parse_intent(raw_reply)

    # Execute intent
    if intent:
        try:
            await _execute_intent(db, reseller, chat, customer, intent)
        except Exception as e:
            log.exception("intent execution failed: %s", e)

    # Send reply over WhatsApp (or dev stub)
    pn_id = wa_cfg.phone_number_id if wa_cfg else None
    token = wa_cfg.access_token_enc if wa_cfg else None
    if clean:
        send_result = await send_text(pn_id, token, phone, clean)
        db.add(Message(chat_id=chat.id, sender="ai", text=clean))


async def _execute_intent(
    db: Session,
    reseller: Reseller,
    chat: Chat,
    customer: Customer,
    intent: dict,
):
    action = intent.get("action")
    if action == "add_item":
        product_id = intent.get("product_id")
        qty = int(intent.get("qty", 1))
        variant_label = intent.get("variant_label")
        variant_id = None
        if variant_label:
            p = db.get(Product, product_id)
            if p:
                v = next((x for x in p.variants if x.label == variant_label), None)
                if v:
                    variant_id = v.id
        draft = list(chat.draft_items or [])
        draft.append({"product_id": product_id, "variant_id": variant_id, "qty": qty})
        chat.draft_items = draft
        return

    if action == "confirm_order":
        items = intent.get("items") or chat.draft_items or []
        address = intent.get("address")
        if not items:
            return
        # resolve variant_label → variant_id if present
        resolved = []
        for it in items:
            pid = it.get("product_id")
            if not pid:
                continue
            vid = it.get("variant_id")
            label = it.get("variant_label")
            if label and not vid:
                p = db.get(Product, pid)
                if p:
                    v = next((x for x in p.variants if x.label == label), None)
                    if v:
                        vid = v.id
            resolved.append({"product_id": pid, "variant_id": vid, "qty": int(it.get("qty", 1))})
        order = create_order_from_items(
            db, reseller, customer, resolved, chat=chat, address=address,
            source=f"WA · {chat.wa_thread_id}",
        )
        await confirm_order(db, reseller, order)
        chat.draft_items = []
