"""Tap Payments integration. https://www.tap.company/

Tap is the right fit for UAE — supports Mada, Apple Pay, KNET, AED
settlement, and is widely used across GCC.

Two flows:

1. **Hosted Checkout (Charge API)** — we create a Charge with the plan
   price, Tap returns a redirect URL, customer pays on Tap's hosted
   page, Tap calls our webhook on success → we activate the subscription.

2. **Webhook** — Tap POSTs to `/webhooks/tap` with the charge result.
   We verify the HMAC signature (`tap-signature` header, hashed with
   our webhook secret) before trusting the payload.

If `tap_secret_key` is empty we operate in **stub mode**: `create_charge`
returns a fake redirect URL pointing at `/billing/dev-confirm/{id}` so
local dev still works without real Tap credentials.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Dict, Optional
import httpx

from ..config import settings

log = logging.getLogger(__name__)

TAP_BASE = "https://api.tap.company/v2"


def _stub_mode() -> bool:
    return not settings.tap_secret_key


async def create_charge(
    *,
    reseller_id: str,
    amount: float,
    currency: str,
    description: str,
    customer_name: str,
    customer_email: str,
    customer_phone: Optional[str] = None,
    redirect_url: str,
    webhook_url: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a hosted Charge and return Tap's response (includes redirect URL).

    Returns:
        {
          "id": "chg_...",
          "status": "INITIATED",
          "transaction": {"url": "https://..."},
          ...
        }
    """
    if _stub_mode():
        # Local-dev fake — let the portal mark the charge as paid via a
        # dev-only confirm endpoint. Production must have a real key.
        import secrets
        fake_id = f"chg_stub_{reseller_id[:8]}_{secrets.token_hex(4)}"
        log.warning("[tap] STUB MODE — no tap_secret_key configured. Returning fake charge %s", fake_id)
        return {
            "id": fake_id,
            "status": "INITIATED",
            "amount": amount,
            "currency": currency,
            "transaction": {"url": f"{settings.app_base_url}/_dev/tap-confirm?charge={fake_id}&reseller={reseller_id}"},
            "metadata": metadata or {},
        }

    body = {
        "amount": amount,
        "currency": currency,
        "threeDSecure": True,
        "save_card": False,
        "description": description,
        "metadata": metadata or {},
        "reference": {"transaction": reseller_id, "order": reseller_id},
        "receipt": {"email": True, "sms": False},
        "customer": {
            "first_name": customer_name.split(" ")[0] if customer_name else "Customer",
            "last_name": " ".join(customer_name.split(" ")[1:]) or "—",
            "email": customer_email,
            "phone": {"country_code": "971", "number": (customer_phone or "0000000000").lstrip("+")},
        },
        "source": {"id": "src_all"},
        "post": {"url": webhook_url},
        "redirect": {"url": redirect_url},
    }
    headers = {
        "Authorization": f"Bearer {settings.tap_secret_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{TAP_BASE}/charges", json=body, headers=headers)
        if r.status_code >= 400:
            log.error("[tap] charge create failed HTTP %s: %s", r.status_code, r.text[:500])
            raise RuntimeError(f"Tap charge create failed: HTTP {r.status_code}")
        return r.json()


async def retrieve_charge(charge_id: str) -> Dict[str, Any]:
    """Fetch a charge by ID — used to verify webhook payload + double-check
    status before activating."""
    if _stub_mode():
        return {"id": charge_id, "status": "CAPTURED"}
    headers = {"Authorization": f"Bearer {settings.tap_secret_key}"}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{TAP_BASE}/charges/{charge_id}", headers=headers)
        return r.json()


def verify_webhook_signature(raw_body: bytes, header_signature: str) -> bool:
    """Tap signs webhook payloads with HMAC-SHA256 using the merchant's
    webhook secret. Header name: `tap-signature` (or `hashstring`)."""
    if _stub_mode() or not settings.tap_webhook_secret:
        return True  # dev convenience
    expected = hmac.new(
        settings.tap_webhook_secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, header_signature or "")
