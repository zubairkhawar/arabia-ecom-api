"""Shopify Admin API client.

We use the Admin API token a reseller generates inside their Shopify
store admin (Apps → Develop apps → Create app → install). Token looks
like `shpat_...` and never expires unless they revoke it. With it we
can:
  - GET /admin/api/{ver}/products.json   → pull catalogue for AI context
  - GET /admin/api/{ver}/orders.json     → pull historical/new orders
  - POST webhooks via Admin API           → register order-created callbacks (later)
"""
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any
import httpx

from ..security import decrypt


def _normalize_domain(d: str) -> str:
    """Accept 'aurora-store', 'aurora-store.myshopify.com', or
    'https://aurora-store.myshopify.com/' and return the canonical
    `<handle>.myshopify.com` form."""
    d = (d or "").strip().lower()
    if d.startswith("http://"):
        d = d[len("http://"):]
    if d.startswith("https://"):
        d = d[len("https://"):]
    d = d.rstrip("/")
    if not d:
        return d
    if "." not in d:
        d = f"{d}.myshopify.com"
    return d


def _headers(token_enc: str) -> Dict[str, str]:
    return {
        "X-Shopify-Access-Token": decrypt(token_enc),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def verify_token(domain: str, access_token: str, api_version: str = "2024-10") -> Dict[str, Any]:
    """Test the token by fetching the shop record. Returns
    {ok, shop_name, error, status}."""
    domain = _normalize_domain(domain)
    url = f"https://{domain}/admin/api/{api_version}/shop.json"
    headers = {
        "X-Shopify-Access-Token": access_token,
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, headers=headers)
            if 200 <= r.status_code < 300:
                shop = r.json().get("shop", {})
                return {
                    "ok": True,
                    "status": r.status_code,
                    "shop_name": shop.get("name"),
                    "currency": shop.get("currency"),
                    "country": shop.get("country_code"),
                }
            return {"ok": False, "status": r.status_code, "error": r.text[:500]}
    except httpx.RequestError as e:
        return {"ok": False, "status": 0, "error": f"network error: {e}"}


async def fetch_products(
    domain: str, access_token_enc: str, api_version: str = "2024-10", limit: int = 250
) -> List[Dict[str, Any]]:
    """Fetch products via Admin REST API. Paginates with `page_info`
    cursor up to `limit` results."""
    domain = _normalize_domain(domain)
    headers = _headers(access_token_enc)
    out: List[Dict[str, Any]] = []
    url = f"https://{domain}/admin/api/{api_version}/products.json?limit=50"
    async with httpx.AsyncClient(timeout=30) as client:
        while url and len(out) < limit:
            r = await client.get(url, headers=headers)
            if r.status_code >= 400:
                raise RuntimeError(f"Shopify fetch_products HTTP {r.status_code}: {r.text[:400]}")
            data = r.json()
            out.extend(data.get("products", []))
            # Parse Link header for pagination
            link = r.headers.get("link") or r.headers.get("Link")
            next_url: Optional[str] = None
            if link:
                # e.g.   <https://shop.myshopify.com/...?page_info=abc>; rel="next"
                for part in link.split(","):
                    part = part.strip()
                    if 'rel="next"' in part:
                        next_url = part.split(";")[0].strip().strip("<>")
                        break
            url = next_url
    return out[:limit]


async def _get_with_retry(
    client: httpx.AsyncClient, url: str, headers: Dict[str, str], max_retries: int = 3
) -> httpx.Response:
    """GET with Shopify rate-limit (429) retry honouring Retry-After.
    Shopify's REST leaky-bucket is 2 req/sec / 40-burst on standard plans;
    sustained backfills can trip 429s mid-flight."""
    for attempt in range(max_retries):
        r = await client.get(url, headers=headers)
        if r.status_code != 429:
            return r
        delay = float(r.headers.get("Retry-After", "2"))
        await asyncio.sleep(min(delay, 10.0))
    return r


def _parse_next_link(link_header: Optional[str]) -> Optional[str]:
    if not link_header:
        return None
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return None


async def fetch_orders(
    domain: str,
    access_token_enc: str,
    api_version: str = "2024-10",
    since: Optional[datetime] = None,
    limit: int = 1000,
) -> List[Dict[str, Any]]:
    """Fetch orders via Admin REST API in created_at ASC order so partial
    progress on rate-limit failure leaves the OLDEST orders written first
    (retry from same `since` re-fetches them — dedup constraint no-ops).

    status=any pulls open/closed/cancelled; default would only return open.
    """
    domain = _normalize_domain(domain)
    headers = _headers(access_token_enc)
    out: List[Dict[str, Any]] = []
    params = "status=any&limit=50&order=created_at%20asc"
    if since:
        # Shopify accepts ISO 8601 with timezone in created_at_min
        params += f"&created_at_min={since.isoformat()}"
    url = f"https://{domain}/admin/api/{api_version}/orders.json?{params}"
    async with httpx.AsyncClient(timeout=30) as client:
        while url and len(out) < limit:
            r = await _get_with_retry(client, url, headers)
            if r.status_code >= 400:
                raise RuntimeError(f"Shopify fetch_orders HTTP {r.status_code}: {r.text[:400]}")
            data = r.json()
            out.extend(data.get("orders", []))
            url = _parse_next_link(r.headers.get("link") or r.headers.get("Link"))
    return out[:limit]
