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
    WhatsAppConfig, PoolNumber,
)
from ..services.ai import (
    build_system_prompt, chat_complete, parse_intent,
    detect_language, heuristic_wants_human,
)
from ..services.notifications import create_notification
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
    is_first_message = len(chat.messages) == 0
    db.add(Message(chat_id=chat.id, sender="customer", text=text, wa_message_id=inbound.get("wa_message_id")))
    chat.unread = (chat.unread or 0) + 1
    db.flush()

    # Notification for the reseller
    create_notification(
        db,
        reseller_id=reseller.id,
        type="new_chat" if is_first_message else "new_message",
        title=("New chat" if is_first_message else "New message")
              + f" from {customer.name or phone}",
        body=text[:200],
        href=f"/reseller/chats?chat={chat.id}",
        meta={"chat_id": chat.id, "customer_phone": phone},
    )

    # Human-handled chats don't auto-reply
    if chat.mode in ("human", "pending_human"):
        return

    # Credit gating — try to consume 1 credit if this inbound starts a
    # fresh 24h conversation. Returns True on continuation or successful
    # consumption. On False (no credits / paused / cancelled), skip the
    # AI reply entirely and notify the reseller once.
    from ..services import credits as credits_service
    if not credits_service.try_consume_for_conversation(
        db, reseller.id, chat_id=chat.id, customer_id=customer.id,
    ):
        create_notification(
            db, reseller_id=reseller.id, type="credits_exhausted",
            title="Out of credits — AI paused",
            body=f"A new conversation from {customer.name or phone} arrived but your plan is out of credits.",
            href="/reseller/billing",
            meta={"chat_id": chat.id, "customer_phone": phone},
        )
        return

    # Belt-and-braces: catch obvious "real agent" requests across EN/AR/RU
    # before paying the LLM round-trip — guarantees no customer is trapped.
    if heuristic_wants_human(text):
        chat.mode = "pending_human"
        db.flush()
        create_notification(
            db, reseller_id=reseller.id, type="escalation",
            title=f"{customer.name or phone} wants a real agent",
            body=text[:200],
            href=f"/reseller/chats?chat={chat.id}",
            meta={"chat_id": chat.id, "reason": "keyword"},
        )
        lang = detect_language(text)
        ack = {
            "english": "Of course! Our team will join in just a moment 🙏",
            "arabic": "بالتأكيد! فريقنا سينضم خلال لحظات 🙏",
            "roman_urdu": "Bilkul! Hamara real agent ek minute mein join karega 🙏",
        }[lang]
        from ..services.wa_credentials import resolve_send_creds
        pn_id, token = resolve_send_creds(db, chat)
        await send_text(pn_id, token, phone, ack)
        db.add(Message(chat_id=chat.id, sender="ai", text=ack))
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

    lang = detect_language(text)
    prompt = build_system_prompt(reseller, ai_settings, catalogue, language=lang)
    raw_reply = await chat_complete(prompt, history, text)
    clean, intent = parse_intent(raw_reply)

    # Execute intent
    if intent:
        try:
            await _execute_intent(db, reseller, chat, customer, intent)
        except Exception as e:
            log.exception("intent execution failed: %s", e)

    # Send reply over WhatsApp using the right credentials —
    # universal pool creds if the chat came via pool, else reseller's own.
    from ..services.wa_credentials import resolve_send_creds
    pn_id, token = resolve_send_creds(db, chat)
    if clean:
        await send_text(pn_id, token, phone, clean)
        db.add(Message(chat_id=chat.id, sender="ai", text=clean))


async def _execute_intent(
    db: Session,
    reseller: Reseller,
    chat: Chat,
    customer: Customer,
    intent: dict,
):
    action = intent.get("action")

    if action == "escalate_to_human":
        chat.mode = "pending_human"
        reason = (intent.get("reason") or "AI escalation")[:200]
        create_notification(
            db, reseller_id=reseller.id, type="escalation",
            title=f"{customer.name or chat.wa_thread_id or 'Customer'} needs a real agent",
            body=reason,
            href=f"/reseller/chats?chat={chat.id}",
            meta={"chat_id": chat.id, "reason": reason},
        )
        return

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
        create_notification(
            db, reseller_id=reseller.id, type="order_confirmed",
            title=f"Order {order.code} confirmed — {order.amount} {order.currency}",
            body=f"{customer.name or chat.wa_thread_id} just placed an order.",
            href=f"/reseller/orders?order={order.code}",
            meta={"order_id": order.id, "order_code": order.code, "chat_id": chat.id},
        )


# ---------------- Universal pool webhook ----------------
# One physical WhatsApp number serves up to 50 resellers. Meta sends
# all inbound for that number to a single URL: /webhooks/wa/pool/{pool_id}.
# We figure out which reseller each message belongs to via the
# [ref:c_xxx] token in the message body (matched to a ClickSession).
# Messages without a ref token get a fallback nudge.


@router.get("/wa/pool/{pool_number_id}")
def wa_pool_verify(
    pool_number_id: str,
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
    db: Session = Depends(get_db),
):
    if hub_mode != "subscribe":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "expected mode=subscribe")
    pool = db.get(PoolNumber, pool_number_id)
    if not pool:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "pool number not found")
    # Single global verify token for pool numbers (settings.wa_verify_token)
    if hub_verify_token != settings.wa_verify_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "bad verify token")
    return Response(content=hub_challenge, media_type="text/plain")


@router.post("/wa/pool/{pool_number_id}")
async def wa_pool_inbound(pool_number_id: str, request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    pool = db.get(PoolNumber, pool_number_id)
    if not pool:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "pool number not found")

    msgs = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for m in value.get("messages", []) or []:
                if m.get("type") != "text":
                    continue
                msgs.append({
                    "wa_message_id": m.get("id"),
                    "from": m.get("from"),
                    "text": m.get("text", {}).get("body", ""),
                    "profile_name": (value.get("contacts", [{}])[0]
                                     .get("profile", {}).get("name") if value.get("contacts") else None),
                })

    if not msgs:
        return {"ok": True, "processed": 0}

    processed = 0
    unattributed = 0
    for inbound in msgs:
        text = inbound.get("text") or ""
        ref = REF_RE.search(text)
        if not ref:
            # Customer messaged the pool number without a ref token —
            # could happen if they save the number from a previous chat.
            # Send a friendly nudge and skip.
            await send_text(
                pool.phone_number_id,
                pool.access_token_enc,
                inbound["from"],
                "Hi! Please tap the product link you saw in our ad to start a chat — that's how we know which store you're messaging about.",
            )
            unattributed += 1
            continue

        click = db.execute(
            select(ClickSession).where(ClickSession.ref_token == ref.group(1))
        ).scalar_one_or_none()
        if not click:
            unattributed += 1
            continue

        reseller = db.get(Reseller, click.reseller_id)
        if not reseller:
            unattributed += 1
            continue

        # Reuse the same handler as own-number — but pass wa_cfg=None
        # because resolve_send_creds will see chat.click_session_id and
        # route outbound via the pool's WABA creds.
        await _handle_inbound_message(db, reseller, None, inbound)
        processed += 1

    db.commit()
    return {"ok": True, "processed": processed, "unattributed": unattributed}



# ===================== Tap Payments webhook =====================

@router.post("/tap")
async def tap_webhook(request: Request, db: Session = Depends(get_db)):
    """Receives charge events from Tap Payments.

    On a captured charge:
      - kind=subscription → activate the paid plan + grant first period's credits
      - kind=topup        → grant the purchased credit bundle
    """
    from ..services import payments_tap, billing as billing_service, credits as credits_service
    from ..models import Payment

    raw = await request.body()
    sig = request.headers.get("tap-signature") or request.headers.get("hashstring") or ""
    if not payments_tap.verify_webhook_signature(raw, sig):
        raise HTTPException(401, "invalid signature")

    payload = await request.json()
    charge_id = payload.get("id")
    tap_status = (payload.get("status") or "").upper()  # CAPTURED|FAILED|...
    metadata = payload.get("metadata") or {}

    if not charge_id:
        raise HTTPException(422, "missing charge id")

    payment = db.execute(
        select(Payment).where(Payment.tap_charge_id == charge_id)
    ).scalar_one_or_none()
    if not payment:
        log.warning("[tap-webhook] unknown charge %s — ignoring", charge_id)
        return {"ok": True, "ignored": True}

    # Idempotency — Tap may retry. Skip if already captured.
    if payment.status == "captured":
        return {"ok": True, "already_captured": True}

    if tap_status != "CAPTURED":
        payment.status = "failed" if tap_status in ("FAILED", "DECLINED", "VOIDED") else payment.status
        db.commit()
        return {"ok": True, "status": payment.status}

    payment.status = "captured"
    reseller_id = payment.reseller_id

    if payment.kind == "subscription":
        plan_code = payment.plan_code or metadata.get("plan_code")
        cycle = (payment.meta or {}).get("billing_cycle") or metadata.get("billing_cycle") or "monthly"
        try:
            billing_service.activate_paid(db, reseller_id, plan_code=plan_code, billing_cycle=cycle)
        except Exception as e:
            log.exception("[tap-webhook] activate failed: %s", e)
            raise HTTPException(500, "activation failed")
    elif payment.kind == "topup":
        amount = payment.credits_granted or (payment.meta or {}).get("credits")
        if not amount:
            raise HTTPException(422, "missing credits amount on topup")
        credits_service.grant(
            db, reseller_id, amount=int(amount), reason="topup_purchase",
            note=f"Top-up purchase: {amount} credits",
        )

    db.commit()
    return {"ok": True, "captured": True}


# ===================== Dev-only Tap confirm (stub mode) =====================

@router.post("/_dev/tap-confirm")
@router.get("/_dev/tap-confirm")
async def tap_dev_confirm(
    charge: str,
    reseller: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Stub-mode convenience: when tap_secret_key is empty, the fake
    Charge redirect URL points here. We synthesize a CAPTURED webhook
    payload so the rest of the flow exercises end-to-end in dev."""
    from ..config import settings as _settings
    if _settings.tap_secret_key:
        raise HTTPException(403, "dev-confirm disabled when Tap is live")

    from ..models import Payment
    payment = db.execute(
        select(Payment).where(Payment.tap_charge_id == charge)
    ).scalar_one_or_none()
    if not payment:
        raise HTTPException(404, "charge not found")
    if payment.status == "captured":
        return {"ok": True, "already_captured": True, "redirect": f"{_settings.frontend_base_url}/reseller/billing?status=success"}

    # Replay webhook handler internally
    from ..services import billing as billing_service, credits as credits_service
    payment.status = "captured"
    if payment.kind == "subscription":
        cycle = (payment.meta or {}).get("billing_cycle") or "monthly"
        billing_service.activate_paid(db, payment.reseller_id, plan_code=payment.plan_code, billing_cycle=cycle)
    elif payment.kind == "topup":
        credits_service.grant(
            db, payment.reseller_id, amount=int(payment.credits_granted or 0),
            reason="topup_purchase",
            note=f"Top-up: {payment.credits_granted} credits (dev confirm)",
        )
    db.commit()
    return {"ok": True, "redirect": f"{_settings.frontend_base_url}/reseller/billing?status=success"}
