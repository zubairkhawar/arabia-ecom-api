"""Meta Conversions API client.

We send server-side events that mirror the browser Pixel event so that
ad-blocker / redirect loss doesn't break attribution. Each event carries
the same `event_id` as the browser Pixel call → Meta dedupes.

Required identifiers per event:
  - fbp / fbc: browser cookie + click cookie (we synthesize fbc from fbclid)
  - client_ip_address, client_user_agent
  - optional hashed user data (phone, email) for stronger match score
"""
import hashlib
import time
import uuid
from typing import Optional, Dict, Any, List
import httpx

from ..config import settings


GRAPH = f"https://graph.facebook.com/{settings.meta_graph_version}"


def gen_event_id() -> str:
    return uuid.uuid4().hex


def fbc_from_fbclid(fbclid: Optional[str], event_time: Optional[int] = None) -> Optional[str]:
    """Synthesize the _fbc cookie value from fbclid as Meta documents:
        fb.subdomainIndex.creationTime.fbclid
    For wa.me / cross-domain we use subdomainIndex=1 and current time."""
    if not fbclid:
        return None
    ts = event_time or int(time.time())
    return f"fb.1.{ts}.{fbclid}"


def _sha256(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def _normalize_phone(raw: Optional[str]) -> Optional[str]:
    """Meta wants phone numbers in E.164 digits-only form (no '+', spaces,
    or dashes) before hashing. e.g. '+971 50 123 4567' → '971501234567'."""
    if not raw:
        return None
    cleaned = "".join(ch for ch in raw if ch.isdigit())
    return cleaned or None


def build_event(
    event_name: str,
    event_id: str,
    fbp: Optional[str],
    fbc: Optional[str],
    client_ip: Optional[str],
    client_ua: Optional[str],
    phone: Optional[str] = None,
    email: Optional[str] = None,
    value: Optional[float] = None,
    currency: Optional[str] = None,
    content_ids: Optional[List[str]] = None,
    contents: Optional[List[Dict[str, Any]]] = None,
    event_source_url: Optional[str] = None,
    action_source: str = "website",
) -> Dict[str, Any]:
    user_data: Dict[str, Any] = {}
    if fbp:
        user_data["fbp"] = fbp
    if fbc:
        user_data["fbc"] = fbc
    if client_ip:
        user_data["client_ip_address"] = client_ip
    if client_ua:
        user_data["client_user_agent"] = client_ua
    if phone:
        normalized = _normalize_phone(phone)
        if normalized:
            user_data["ph"] = [_sha256(normalized)]
    if email:
        user_data["em"] = [_sha256(email)]

    custom_data: Dict[str, Any] = {}
    if value is not None:
        custom_data["value"] = round(float(value), 2)
    if currency:
        custom_data["currency"] = currency
    if content_ids:
        custom_data["content_ids"] = content_ids
    if contents:
        custom_data["contents"] = contents

    evt: Dict[str, Any] = {
        "event_name": event_name,
        "event_time": int(time.time()),
        "event_id": event_id,
        "action_source": action_source,
        "user_data": user_data,
    }
    if event_source_url:
        evt["event_source_url"] = event_source_url
    if custom_data:
        evt["custom_data"] = custom_data
    return evt


async def send_event(
    pixel_id: str,
    access_token: str,
    event: Dict[str, Any],
    test_event_code: Optional[str] = None,
) -> Dict[str, Any]:
    """POST one event to /{pixel_id}/events. Returns the API response and
    HTTP status. Never raises — caller decides what to do with a failure."""
    url = f"{GRAPH}/{pixel_id}/events"
    body: Dict[str, Any] = {"data": [event], "access_token": access_token}
    if test_event_code:
        body["test_event_code"] = test_event_code
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json=body)
            return {"status": r.status_code, "body": r.text}
    except httpx.RequestError as e:
        return {"status": 0, "body": f"network error: {e}"}
