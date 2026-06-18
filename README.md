# Arabia Ecom API

FastAPI backend for the AI Order Portal — WhatsApp + Shopify AI order
orchestration with multi-platform ad attribution.

## What it does

- **Auth**: signup / login / JWT.
- **Channel setup**: each reseller can connect their **own** Meta WhatsApp
  number or be assigned a slot on a **universal pool number** (50 resellers
  per number, auto-spillover).
- **Meta Pixel + CAPI**: each reseller plugs in their own Pixel ID +
  Conversions API access token. The backend mirrors browser pixel events
  server-side so attribution doesn't break on redirects or ad-blockers.
- **Products**: full Shopify-style options/variants, bundle tiers
  (qty-for-price), discounts (percent or flat), and a pricing engine that
  correctly stacks variant override → bundle → discount.
- **The critical link flow**: `/links/resolve/{slug}` returns the Pixel ID
  + a wa.me deeplink containing a short ref token. `/links/click` writes a
  `click_session` (fbclid/fbp/fbc/ttclid/gclid/utm) and fires AddToCart via
  Meta CAPI. The WhatsApp webhook matches the inbound message's ref token
  to that session, so the conversation is attributed end-to-end.
- **AI**: OpenAI agent with a per-reseller system prompt. Replies have an
  optional trailing `INTENT:` JSON block the backend executes (add item,
  confirm order). On order confirmation, a server-side **Purchase** event
  fires to Meta CAPI with the stored `fbc`/`fbp`/`fbclid`.
- **Orders**: status + independent **delivery status** lifecycle
  (pending → dispatched → in_transit → delivered → returned), tracking
  numbers, CSV import/export.
- **Templates**: WhatsApp message templates with approval status
  (mock approval flow now; real Meta submission API is Phase 1.5).
- **Follow-ups**: per-order template send via WA Cloud API; only Approved
  templates may be sent.
- **Tracking dashboard**: per-platform (TikTok / Meta / Snapchat / Google /
  Other) clicks, orders, delivered, returned + product×platform breakdown.
- **Admin**: cross-reseller views, pool number management, admin access
  toggle.
- **Billing**: plan tiers (Silver / Gold / Platinum), usage counters.
  No real payments — stub field on the reseller.

## Stack

- FastAPI + Uvicorn
- PostgreSQL via SQLAlchemy 2.0 + Alembic
- Pydantic v2 / pydantic-settings
- httpx (Meta Graph + WhatsApp Cloud API)
- OpenAI Python SDK
- bcrypt (passwords) + python-jose (JWT) + Fernet (token encryption)

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env
# Edit .env: paste your DATABASE_URL, generate FERNET_KEY + JWT_SECRET
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(48))"

alembic upgrade head
python scripts/seed.py

uvicorn app.main:app --reload
```

Open http://localhost:8000/docs for Swagger.

## Tests

```bash
pytest                  # full suite
pytest tests/test_pricing.py -v   # pure unit tests (no DB)
```

Tests run against the configured `DATABASE_URL` and clean up after
themselves. The pricing tests are pure unit tests (no DB).

## Deploying to Render

The `render.yaml` blueprint provisions the Postgres DB + the FastAPI web
service. After connecting your GitHub repo:

1. Push to `main`.
2. Render auto-runs the build (`pip install -r requirements.txt && alembic upgrade head`).
3. Set the secret env vars (`FERNET_KEY`, `OPENAI_API_KEY`, `WA_VERIFY_TOKEN`)
   in the Render dashboard.
4. After first deploy, run `python scripts/seed.py` from the Render shell.

## The attribution flow in detail

```
Facebook Ad → click
  ↓
Vercel /r/[slug]          ← OUR page (frontend renders pixel snippet)
  ↓
  ├─ GET /links/resolve/{slug}    → pixel_id, ref_token, wa_deeplink
  ├─ fbq('track','AddToCart')     → browser pixel fires
  ├─ POST /links/click            → click_session row + CAPI AddToCart
  │                                  (server-side mirror, same event_id)
  ↓ ~300ms hold (lets pixel beacon flush)
window.location = wa.me/{number}?text=...%20[c_xxxxxxxx]
  ↓
WhatsApp Open-in-app prompt (Meta's own UI)
  ↓
Customer sends message: "Hi I'm interested in {product} [c_xxxxxxxx]"
  ↓
POST /webhooks/wa/{reseller_id}   ← Meta Cloud API webhook
  ↓
  ├─ parse [c_xxx] → look up ClickSession → attach to Chat
  ├─ AI handler (OpenAI) → reply + optional INTENT block
  ├─ if INTENT: confirm_order → create Order → confirm → fire Purchase CAPI
  │                              (with stored fbp/fbc/fbclid → Meta attributes
  │                               the conversion to the original ad click)
  └─ send reply via WA Cloud API
```

The browser AddToCart and the CAPI AddToCart share the same `event_id` so
Meta dedupes — this is what protects you when ad-blockers or the redirect
itself kill the browser beacon. The Purchase event can only fire
server-side (no browser at confirmation time), and CAPI uses the stored
`fbc`/`fbp` to attribute back to the click. This is the only reliable way
to get WhatsApp purchases attributed in Meta Ads Manager.

## Endpoints

See `/docs` for the full OpenAPI spec. High-level groups:

- `/auth` — signup, login, me
- `/me/*` — reseller profile, AI settings, Meta + WhatsApp config
- `/products` — CRUD, quote (pricing engine)
- `/links` — `/resolve/{slug}`, `/click`
- `/webhooks/wa/{reseller_id}` — Meta WhatsApp Cloud API webhook
- `/chats` — list/get/mode/human-reply
- `/orders` — CRUD, follow-up send, CSV import/export
- `/templates` — CRUD + admin approval
- `/tracking/overview` — multi-platform attribution dashboard
- `/admin/*` — cross-reseller views, pool numbers, admin users
- `/billing/*` — plan overview, upgrade (stub), plan management

## Phase 1.5

Things wired with stubs / mocks now, ready to be filled in:
- TikTok / Snap / Google CAPI server-side dispatchers (interface in `services/attribution.py`).
- Real Meta template submission API (currently mock-approval via admin endpoint).
- Real WA Cloud API send when reseller hasn't configured credentials (currently dev-stub).
- Subscription payments — currently just a `plan` field on Reseller.
