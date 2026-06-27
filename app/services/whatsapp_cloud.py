"""WhatsApp Cloud API client.

Reseller flow:
  - own number: use reseller's WhatsAppConfig (phone_number_id + access_token)
  - universal: use the PoolNumber's (waba_id + phone_number_id + access_token)

When credentials are absent we log + return a stub success — so dev/demo
flows still complete without a live WABA. In prod, missing credentials
return a clear error.
"""
import logging
from typing import Optional, Dict, Any
import httpx

from ..config import settings
from ..security import decrypt

log = logging.getLogger(__name__)

GRAPH = f"https://graph.facebook.com/{settings.meta_graph_version}"


async def verify_creds(
    phone_number_id: str,
    access_token: str,
) -> Dict[str, Any]:
    """Test that the access token can read the phone number's metadata.
    GET https://graph.facebook.com/{ver}/{phone_number_id} → 200 with
    display number / verified name on success, 4xx on failure."""
    url = f"{GRAPH}/{phone_number_id}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                params={"fields": "id,display_phone_number,verified_name,quality_rating"},
            )
            ok = 200 <= r.status_code < 300
            return {"ok": ok, "status": r.status_code, "body": r.text[:800]}
    except httpx.RequestError as e:
        return {"ok": False, "status": 0, "body": f"network error: {e}"}


async def send_text(
    phone_number_id: Optional[str],
    access_token_enc: Optional[str],
    to: str,
    text: str,
) -> Dict[str, Any]:
    """Send a plain text message. Returns {ok, status, body}."""
    if not phone_number_id or not access_token_enc:
        log.info("[whatsapp_cloud] dev stub send to %s: %s", to, text[:60])
        return {"ok": True, "status": 0, "body": "dev-stub (no WA creds configured)"}

    token = decrypt(access_token_enc)
    url = f"{GRAPH}/{phone_number_id}/messages"
    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                url, json=body, headers={"Authorization": f"Bearer {token}"}
            )
            return {"ok": 200 <= r.status_code < 300, "status": r.status_code, "body": r.text}
    except httpx.RequestError as e:
        return {"ok": False, "status": 0, "body": f"network error: {e}"}


async def send_template(
    phone_number_id: Optional[str],
    access_token_enc: Optional[str],
    to: str,
    template_name: str,
    language: str = "en",
    components: Optional[list] = None,
) -> Dict[str, Any]:
    if not phone_number_id or not access_token_enc:
        log.info("[whatsapp_cloud] dev stub template %s → %s", template_name, to)
        return {"ok": True, "status": 0, "body": "dev-stub (no WA creds configured)"}
    token = decrypt(access_token_enc)
    url = f"{GRAPH}/{phone_number_id}/messages"
    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
        },
    }
    if components:
        body["template"]["components"] = components
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                url, json=body, headers={"Authorization": f"Bearer {token}"}
            )
            return {"ok": 200 <= r.status_code < 300, "status": r.status_code, "body": r.text}
    except httpx.RequestError as e:
        return {"ok": False, "status": 0, "body": f"network error: {e}"}
