"""Credit accounting.

A *credit* is one AI-handled WhatsApp conversation. A conversation is the
24-hour window of activity with a single customer — within that window,
the customer can send N messages and the AI replies N times for the same
1 credit. After 24h of silence, the next inbound message starts a fresh
conversation = 1 new credit.

This module is the only place that mutates Subscription.credits_balance.
Every change is mirrored as a CreditLedger row so we have a complete
audit trail (and the cached balance can be rebuilt at any time).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import select, func

from ..models import Subscription, CreditLedger, Chat, Message


CONVERSATION_WINDOW = timedelta(hours=24)


def get_or_init_subscription(db: Session, reseller_id: str) -> Subscription:
    """Read the reseller's subscription row. Caller should ensure one
    exists (created at signup via billing.start_trial)."""
    sub = db.execute(
        select(Subscription).where(Subscription.reseller_id == reseller_id)
    ).scalar_one_or_none()
    return sub  # type: ignore[return-value]


def _ledger_write(
    db: Session,
    reseller_id: str,
    delta: int,
    reason: str,
    note: Optional[str] = None,
    customer_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    balance_after: int = 0,
) -> None:
    db.add(CreditLedger(
        reseller_id=reseller_id,
        delta=delta,
        reason=reason,
        note=note,
        customer_id=customer_id,
        chat_id=chat_id,
        balance_after=balance_after,
    ))


def grant(
    db: Session,
    reseller_id: str,
    amount: int,
    reason: str,
    note: Optional[str] = None,
) -> int:
    """Add credits to a subscription. Returns new balance."""
    if amount <= 0:
        raise ValueError("grant amount must be positive")
    sub = get_or_init_subscription(db, reseller_id)
    if not sub:
        raise ValueError(f"no subscription for reseller {reseller_id}")
    sub.credits_balance = (sub.credits_balance or 0) + amount
    sub.credits_granted_this_period = (sub.credits_granted_this_period or 0) + amount
    _ledger_write(
        db, reseller_id, delta=amount, reason=reason, note=note,
        balance_after=sub.credits_balance,
    )
    db.flush()
    return sub.credits_balance


def reset_period(
    db: Session,
    reseller_id: str,
    plan_credits: int,
    reason: str = "period_renewal",
) -> int:
    """Refill at start of a new billing period. Resets per-period counters."""
    sub = get_or_init_subscription(db, reseller_id)
    if not sub:
        raise ValueError(f"no subscription for reseller {reseller_id}")
    # Unused credits do NOT roll over — set balance to plan_credits.
    sub.credits_balance = plan_credits
    sub.credits_granted_this_period = plan_credits
    sub.credits_used_this_period = 0
    _ledger_write(
        db, reseller_id, delta=plan_credits, reason=reason,
        note="period renewed; balance reset to plan grant",
        balance_after=sub.credits_balance,
    )
    db.flush()
    return sub.credits_balance


def _is_fresh_conversation(db: Session, chat_id: str) -> bool:
    """Has it been >24h since the last message on this chat? If yes, this
    inbound starts a new conversation (= new credit)."""
    last = db.execute(
        select(func.max(Message.created_at)).where(Message.chat_id == chat_id)
    ).scalar_one_or_none()
    if last is None:
        return True
    # `last` is the message we just inserted (timestamp ~now). We want to
    # know whether the SECOND-most-recent message was >24h ago.
    second_last = db.execute(
        select(Message.created_at)
        .where(Message.chat_id == chat_id)
        .order_by(Message.created_at.desc())
        .limit(2)
    ).scalars().all()
    if len(second_last) < 2:
        return True
    prev = second_last[1]
    now = datetime.now(timezone.utc)
    if prev.tzinfo is None:
        prev = prev.replace(tzinfo=timezone.utc)
    return (now - prev) > CONVERSATION_WINDOW


def try_consume_for_conversation(
    db: Session,
    reseller_id: str,
    chat_id: str,
    customer_id: Optional[str] = None,
) -> bool:
    """Charge 1 credit if this inbound starts a fresh 24h conversation.
    Returns True if the AI is allowed to reply (either: credit was
    consumed OR this is a continuation of an already-paid conversation).
    Returns False only when a fresh conversation needs to be started but
    the reseller has no credits — caller should skip the AI reply.

    Subscriptions in status 'cancelled' or 'paused' always return False
    even if balance > 0 (paused means: don't process new conversations).
    """
    sub = get_or_init_subscription(db, reseller_id)
    if not sub:
        # No subscription record — treat as paused
        return False
    if sub.status in ("cancelled", "paused"):
        return False
    if sub.status == "past_due":
        # 3-day grace already consumed elsewhere; treat as paused
        return False

    fresh = _is_fresh_conversation(db, chat_id)
    if not fresh:
        return True  # continuation — already paid for

    if (sub.credits_balance or 0) <= 0:
        return False

    sub.credits_balance -= 1
    sub.credits_used_this_period = (sub.credits_used_this_period or 0) + 1
    _ledger_write(
        db, reseller_id, delta=-1, reason="conversation",
        note=f"new 24h conversation",
        customer_id=customer_id,
        chat_id=chat_id,
        balance_after=sub.credits_balance,
    )
    db.flush()
    return True


def current_balance(db: Session, reseller_id: str) -> int:
    sub = get_or_init_subscription(db, reseller_id)
    return (sub.credits_balance if sub else 0) or 0
