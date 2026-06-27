"""LLM-driven WhatsApp sales agent.

Replies are 100% LLM (OpenAI gpt-4o by default — configurable). The agent:

  - detects language per message (English / Arabic / Roman Urdu)
    and ALWAYS replies in that same language
  - has the reseller's live product catalogue baked into the prompt,
    so it can answer questions, recommend, upsell, handle bundles
    and discounts
  - emits structured INTENT lines at the END of replies so the backend
    can act: add_item, confirm_order, escalate_to_human
  - 'escalate_to_human' fires when the customer asks for a real agent,
    is frustrated, or asks something the AI can't answer confidently —
    the chat then flips to pending_human and auto-reply stops
"""
import json
import logging
import re
from typing import Optional, List, Dict, Any, Literal
from openai import AsyncOpenAI

from ..config import settings
from ..models import AISetting, Product, Reseller

log = logging.getLogger(__name__)

_client: Optional[AsyncOpenAI] = None


def _get_client() -> Optional[AsyncOpenAI]:
    global _client
    if _client is None and settings.openai_api_key:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


Language = Literal["english", "arabic", "roman_urdu"]


# -------- Language detection --------


_ARABIC_BLOCK = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")

# Common Roman-Urdu / Hindi-romanized markers. Case-insensitive substring check.
_ROMAN_URDU_TOKENS = {
    "kya", "hai", "mujhe", "mujhay", "salam", "assalam", "assalamualaikum",
    "shukria", "shukriya", "bhai", "bhaijan", "apka", "aapka", "aap", "tum",
    "kahan", "kab", "kese", "kaise", "kitne", "kitna", "yeh", "wo", "woh",
    "mein", "hum", "humare", "hamara", "krna", "karna", "krdo", "kardo",
    "bata", "btao", "btado", "chahiye", "chahye", "lazmi", "zaroori", "abhi",
    "bhejdo", "bhej do", "address", "delivery", "order", "pakka", "pakka karo",
    "bilkul", "theek", "thik", "acha", "achha", "haan", "ji haan", "ji nahi",
    "nahin", "nai", "agla", "agla din", "kal", "aaj", "kitne din",
    "discount do", "rate kya", "kitne ka", "kitne ka hai",
}


def detect_language(text: str) -> Language:
    if not text:
        return "english"
    if _ARABIC_BLOCK.search(text):
        return "arabic"
    low = text.lower()
    # Count how many Roman-Urdu tokens appear; ≥2 distinct tokens → roman_urdu
    matches = sum(1 for t in _ROMAN_URDU_TOKENS if t in low)
    if matches >= 2:
        return "roman_urdu"
    return "english"


# -------- System prompt --------


_LANG_INSTRUCTION = {
    "english": "Always reply in clear, friendly English.",
    "arabic": (
        "Always reply in fluent Modern Standard Arabic with a friendly Khaleeji touch. "
        "Use Arabic numerals (٠-٩) only if the customer used them; otherwise keep numbers as digits."
    ),
    "roman_urdu": (
        "Always reply in friendly Roman Urdu (Urdu written in English script — like 'shukria, kal aapko bhej deta hoon'). "
        "Never reply in Hindi Devanagari or Urdu script — keep it Roman."
    ),
}


def _format_price(p: Product) -> str:
    price = f"{p.price} {p.currency}"
    if p.discount_type == "percent" and p.discount_value:
        price += f" (−{int(p.discount_value)}%)"
    elif p.discount_type == "flat" and p.discount_value:
        price += f" (−{p.discount_value} {p.currency} off)"
    return price


def _catalog_block(catalogue: List[Product]) -> str:
    if not catalogue:
        return "(no products yet)"
    lines = []
    for p in catalogue[:50]:
        kp = " · ".join((p.key_points or [])[:4])
        bundles = ", ".join(
            f"{b.qty}-for-{b.price}" for b in (p.bundles or [])
        )
        variants = ", ".join((v.label for v in (p.variants or [])[:8]))
        parts = [f"- id={p.id}", p.name, _format_price(p)]
        if kp:
            parts.append(kp)
        if variants:
            parts.append(f"variants: {variants}")
        if bundles:
            parts.append(f"bundles: {bundles}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def build_system_prompt(
    reseller: Reseller,
    ai: AISetting,
    catalogue: List[Product],
    language: Language = "english",
) -> str:
    lang_rule = _LANG_INSTRUCTION[language]
    persona = ai.tone.lower() if ai.tone else "friendly"
    name = ai.ai_name or "Max"
    convince = (
        "If the customer hesitates, asks to think, or goes quiet, send a warm soft-incentive "
        "follow-up (free shipping if they confirm today, etc.)."
        if ai.convince_hesitant else ""
    )

    return f"""You are {name}, a {persona} WhatsApp sales agent for {reseller.name}.

LANGUAGE: {lang_rule}
- Detect the customer's language each message: English, Arabic, or Roman Urdu.
- Always reply in the SAME language the customer just used. Never mix.

CATALOGUE (id | name | price | key_points | variants | bundles):
{_catalog_block(catalogue)}

YOUR JOB:
1. Greet warmly and answer product questions accurately from the catalogue above.
2. Collect the customer's name (if not visible), phone (already known from WhatsApp), delivery address, and preferred variant.
3. When they say yes / confirm / okay-order-it, finalize the order.
4. {convince}

RULES:
- Stay grounded in the catalogue. Never invent products, prices, or specs.
- Currency is {reseller.currency}. Always quote prices honestly with the discount applied if any.
- Keep replies short for WhatsApp — 1-3 short sentences usually.
- Use 1-2 emojis max per reply. Don't emoji-stuff.
- If you don't know the answer or the customer seems frustrated → escalate.

STRUCTURED INTENTS (machine-readable — invisible to customer):
At the END of your reply (after the natural-language reply, on its own line), you MAY emit ONE of:

  INTENT: {{"action":"add_item","product_id":"<id>","variant_label":"<optional>","qty":<int>}}
  INTENT: {{"action":"confirm_order","items":[{{"product_id":"<id>","variant_label":"<optional>","qty":<int>}}],"address":"<one-line address>"}}
  INTENT: {{"action":"escalate_to_human","reason":"<short reason>"}}

When to emit each:
- add_item — when the customer just decided which item/variant/qty (but you still need address etc.).
- confirm_order — ONLY when name + address + items + qty are all confirmed by the customer.
- escalate_to_human — when:
   a) the customer literally asks for a real agent / human / person ("speak to someone", "real agent", "human please", "asli banda", "agent se baat karwao", "اريد محادثة شخص حقيقي" etc.), OR
   b) they have a complaint / dispute / refund question, OR
   c) you've failed to answer the same question twice, OR
   d) they're clearly frustrated / using all caps / cursing.

The INTENT line is stripped before sending to the customer. Always put a natural reply BEFORE the INTENT line.

EXAMPLE replies:

(Customer asks in English) "Do the earbuds come in white?"
Yes! White is in stock 🎧 They're {{price}} — want me to set one aside for you?
INTENT: {{"action":"add_item","product_id":"<id>","variant_label":"White","qty":1}}

(Customer asks in Arabic) "كم سعر السماعات؟"
السماعات بـ 199 درهم وفيها خصم 10% اليوم 🎧 تبي اطلبها؟

(Customer in Roman Urdu) "asli banda chahiye"
Bilkul! Aapko ek hi minute mein hamara real agent join karega 🙏
INTENT: {{"action":"escalate_to_human","reason":"customer asked for real agent"}}
"""


# -------- Intent extraction --------


_INTENT_RE = re.compile(r"INTENT:\s*(\{.*\})\s*$", re.MULTILINE | re.DOTALL)


def parse_intent(reply: str) -> tuple[str, Optional[Dict[str, Any]]]:
    """Strip the trailing INTENT JSON. Returns (customer_facing_text, intent_or_None)."""
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


# -------- Heuristic fallback for "real agent" --------
# In case the LLM doesn't emit the escalate intent, catch obvious requests
# in EN / Arabic / Roman Urdu so the customer is never trapped with the bot.


_REAL_AGENT_PATTERNS = [
    # English
    "real agent", "real person", "human agent", "real human",
    "talk to human", "talk to a human", "talk to an agent",
    "speak to human", "speak to a human", "speak to someone",
    "speak to a person", "speak to agent", "live agent",
    "customer support", "customer care", "support agent",
    "actual person", "actual human",
    # Roman Urdu
    "asli banda", "asli aadmi", "asli agent", "real banda",
    "kisi real banday", "agent se baat", "agent se bat",
    "insan se baat", "support se baat",
    # Arabic
    "شخص حقيقي", "موظف حقيقي", "اريد محادثة شخص",
    "ابي اكلم انسان", "وكيل حقيقي", "اتكلم مع موظف",
    "خدمة العملاء",
]


def heuristic_wants_human(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(p in low for p in _REAL_AGENT_PATTERNS)


# -------- Chat completion --------


async def chat_complete(
    system_prompt: str,
    history: List[Dict[str, str]],
    user_message: str,
) -> str:
    client = _get_client()
    if not client:
        log.info("[ai] no OPENAI_API_KEY — returning dev stub reply")
        return "Thanks for your message! I'll get back to you shortly. (dev stub)"
    msgs = [{"role": "system", "content": system_prompt}]
    msgs.extend(history)
    msgs.append({"role": "user", "content": user_message})
    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=msgs,
        temperature=0.7,
    )
    return resp.choices[0].message.content or ""
