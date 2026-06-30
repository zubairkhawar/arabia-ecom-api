from typing import Optional
from sqlalchemy import String, Integer, Float, ForeignKey, JSON, Date, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from datetime import date, datetime

from ..db import Base
from ._base import IdMixin, TimestampMixin


class Plan(Base, IdMixin, TimestampMixin):
    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    price: Mapped[float] = mapped_column(Float, default=0.0)  # monthly price in AED
    price_annual: Mapped[float] = mapped_column(Float, default=0.0)  # 12-month price in AED
    currency: Mapped[str] = mapped_column(String(8), default="AED")
    # Credits-based caps
    monthly_credits: Mapped[int] = mapped_column(Integer, default=0)
    # Legacy caps — kept for backwards-compat reads
    orders_cap: Mapped[Optional[int]] = mapped_column(Integer)
    conversations_cap: Mapped[Optional[int]] = mapped_column(Integer)
    stores_cap: Mapped[Optional[int]] = mapped_column(Integer)
    universal_numbers_cap: Mapped[Optional[int]] = mapped_column(Integer)
    features: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Subscription(Base, IdMixin, TimestampMixin):
    """One row per reseller. Tracks plan, status, period, and credit balance.

    Status lifecycle:
      trial → active → past_due → cancelled
      trial can transition directly to paused if trial credits run out and
      user doesn't pick a paid plan within the trial window.
    """
    __tablename__ = "subscriptions"

    reseller_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resellers.id", ondelete="CASCADE"), unique=True, index=True
    )
    plan_code: Mapped[str] = mapped_column(String(32), default="trial")
    status: Mapped[str] = mapped_column(String(16), default="trial", index=True)
    billing_cycle: Mapped[str] = mapped_column(String(8), default="monthly")  # monthly|annual

    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    current_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Cached balance for fast reads — authoritative truth is the CreditLedger
    credits_balance: Mapped[int] = mapped_column(Integer, default=0)
    credits_granted_this_period: Mapped[int] = mapped_column(Integer, default=0)
    credits_used_this_period: Mapped[int] = mapped_column(Integer, default=0)

    # Payment provider linkage (Tap Payments)
    tap_customer_id: Mapped[Optional[str]] = mapped_column(String(64))
    tap_subscription_id: Mapped[Optional[str]] = mapped_column(String(64))


class CreditLedger(Base, IdMixin, TimestampMixin):
    """Append-only log of every credit grant or consumption. Source of truth.
    Subscription.credits_balance is a cache derived from this.

    delta > 0 → grant (plan refill, trial seed, top-up, admin grant)
    delta < 0 → consumption (one conversation processed)
    """
    __tablename__ = "credit_ledger"

    reseller_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resellers.id", ondelete="CASCADE"), index=True
    )
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    # Human-readable note shown in usage history. Optional.
    note: Mapped[Optional[str]] = mapped_column(String(255))
    # Optional FK-like pointers (no FK to keep the ledger immutable even
    # if the referenced row gets deleted)
    customer_id: Mapped[Optional[str]] = mapped_column(String(32))
    chat_id: Mapped[Optional[str]] = mapped_column(String(32))
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)


class Payment(Base, IdMixin, TimestampMixin):
    """Record of a successful Tap payment (subscription charge or top-up)."""
    __tablename__ = "payments"

    reseller_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resellers.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))  # subscription|topup
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="AED")
    status: Mapped[str] = mapped_column(String(16), default="captured")  # initiated|captured|failed|refunded
    tap_charge_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    plan_code: Mapped[Optional[str]] = mapped_column(String(32))
    credits_granted: Mapped[Optional[int]] = mapped_column(Integer)
    meta: Mapped[Optional[dict]] = mapped_column(JSON)


class Usage(Base, IdMixin, TimestampMixin):
    """Legacy monthly usage counter — kept so existing /billing/overview
    endpoint and its consumers don't break. New code should read from
    Subscription.credits_used_this_period instead."""
    __tablename__ = "usages"

    reseller_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resellers.id", ondelete="CASCADE"), index=True
    )
    cycle_start: Mapped[date] = mapped_column(Date, nullable=False)
    cycle_end: Mapped[date] = mapped_column(Date, nullable=False)
    orders_used: Mapped[int] = mapped_column(Integer, default=0)
    conversations_used: Mapped[int] = mapped_column(Integer, default=0)
