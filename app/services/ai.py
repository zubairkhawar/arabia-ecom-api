"""OpenAI-driven AI agent for WhatsApp sales chats.

The model is instructed to either:
  - reply naturally (and we send the reply), OR
  - emit a JSON intent (e.g. add_item, set_address, confirm_order) which the
    backend executes. The intent payload lives inside the reply so we can
    parse + strip it before forwarding.
"""
import json
import logging
import re
from typing import Optional, List, Dict, Any
from openai import AsyncOpenAI

from ..config import settings
from ..models import AISetting, Product, Chat, Reseller

log = logging.getLogger(__name__)

_client: Optional[AsyncOpenAI] = None


def _get_client() -> Optional[AsyncOpenAI]:
    global _client
    if _client is None and settings.openai_api_key:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


def build_system_prompt(reseller: Reseller, ai: AISetting, catalogue: List[Product]) -> str:
    catalog_lines = []
    for p in catalogue:
        kp = " · ".join(p.key_points or [])
        catalog_lines.append(
            f"- {p.id} | {p.name} | {p.price} {p.currency}"
            + (f" | {kp}" if kp else "")
            + (f" | variants: " + ", ".join(v.label for v in p.variants) if p.variants else "")
        )
    catalog = "\n".join(catalog_lines) or "(no products)"
    convince = (
        "\nIf the customer goes quiet or hesitates, send a friendly follow-up with a soft incentive."
        if ai.convince_hesitant else ""
    )
    human = "\nWrite like a human. Avoid robotic phrasing and emoji-stuffing." if ai.always_sound_human else ""
    return f"""You are {ai.ai_name}, a {ai.tone.lower()} sales assistant for {reseller.name}.
Role: {ai.role}
Response length: {ai.response_length}.

You help customers via WhatsApp:
- answer product questions
- collect their address, phone, and preferred variant
- confirm the final order

Catalogue (id | name | price | key_points | variants):
{catalog}

When the customer is ready to buy and has given a deliverable address, you must emit a single line at the END of your reply (only after a confirming sentence):
    INTENT: {{"action":"confirm_order","items":[{{"product_id":"<id>","variant_label":"<optional>","qty":<int>}}],"address":"<one-line address>"}}

Other intents you may emit instead of confirm_order:
    INTENT: {{"action":"add_item","product_id":"<id>","variant_label":"<optional>","qty":<int>}}
    INTENT: {{"action":"need_info","missing":"address|variant|qty"}}

Rules:
- ALWAYS write a short friendly customer-facing reply first; the INTENT line is invisible to the customer (we strip it).
- Never invent products not in the catalogue.
- Currency is {reseller.currency}. Quote prices honestly.
- Use the customer's language if they switch to Arabic/Urdu/etc (you support {", ".join(ai.supported_languages or ['English'])}).
{convince}{human}
"""


_INTENT_RE = re.compile(r"INTENT:\s*(\{.*\})\s*$", re.MULTILINE | re.DOTALL)


def parse_intent(reply: str) -> tuple[str, Optional[Dict[str, Any]]]:
    """Strip and parse the trailing INTENT JSON block. Returns (clean_text, intent_or_None)."""
    m = _INTENT_RE.search(reply)
    if not m:
        return reply.strip(), None
    raw = m.group(1)
    try:
        intent = json.loads(raw)
    except Exception:
        return reply.strip(), None
    clean = _INTENT_RE.sub("", reply).strip()
    return clean, intent


async def chat_complete(
    system_prompt: str,
    history: List[Dict[str, str]],
    user_message: str,
) -> str:
    client = _get_client()
    if not client:
        # Dev fallback so flows work without OPENAI_API_KEY
        log.info("[ai] no OPENAI_API_KEY — returning dev stub reply")
        return f"Thanks for your message! I'll get back to you shortly. (dev stub)"
    msgs = [{"role": "system", "content": system_prompt}]
    msgs.extend(history)
    msgs.append({"role": "user", "content": user_message})
    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=msgs,
        temperature=0.7,
    )
    return resp.choices[0].message.content or ""
