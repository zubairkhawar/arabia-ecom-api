# Arabia Ecom — Engineering Handoff

You're picking up an in-progress WhatsApp + Shopify AI order-orchestration platform for resellers in the Gulf (UAE / KSA / Pakistan). This document is the single thing you need to read before opening the code.

The product is shipped and live. The current owner is **Safdar** (client / CEO). The previous engineer is **Zubair Khawar** (`zubairkhawer@gmail.com`).

---

## 1 · Two repos

| Repo | Role | Lang / Stack | Hosted on |
|---|---|---|---|
| `zubairkhawar/arabia-ecom-api` | FastAPI backend, Postgres, all business logic, Meta + WhatsApp + Shopify + OpenAI integrations | Python 3.11, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2 | **Render** (Singapore region) |
| `zubairkhawar/arabiaEcom` (folder name: `ai-order-portal`) | Next.js portal — both Admin and Reseller UIs are in the same app, role-gated by `kind` field on the JWT | Next.js 16 (App Router) + TypeScript + Tailwind v4 + Turbopack | **Vercel** |

Push to `main` on either → auto-deploys to its host. Render runs `alembic upgrade head` as part of the build, so DB migrations apply on push.

There's no monorepo. Clone both side-by-side:
```
~/work/arabia-ecom-api      ← backend, you'll spend 70% of your time here
~/work/ai-order-portal      ← frontend
```

**Production URLs**
- Backend API: `https://arabia-ecom-api.onrender.com` (cold-start ~30s if idle)
- Portal: `https://arabia-ecom.vercel.app`
- Meta WhatsApp webhook: `https://arabia-ecom-api.onrender.com/webhooks/wa/{reseller_id}` (own number) or `/webhooks/wa/pool/{pool_number_id}` (universal pool)

---

## 2 · What the product does (60-second version)

A reseller signs up, connects either:
- their **own** Meta WhatsApp Business number, **or**
- gets auto-assigned a slot on a **universal pool number** (capacity 50 resellers per number)

…then connects their **Meta Pixel + Conversions API token** and a **Shopify store**. They run Facebook/TikTok/Snap/Google ads pointing at our `/r/{slug}` redirect URL. The redirect:

1. Fires `InitiateCheckout` via the browser Pixel
2. Writes a `click_session` (with `fbclid` / `fbp` / `fbc` / `utm_*` / `wa_number` / `pool_number_id`)
3. Sends a mirror CAPI `InitiateCheckout` server-side (same `event_id` for dedupe)
4. Redirects to `wa.me/{number}?text=…[c_xxxxxxxx]`

The customer messages WhatsApp. The Cloud API webhook fires our `/webhooks/wa/...`. We parse `[c_xxx]`, look up the `click_session`, attach it to a `Chat`, and an OpenAI agent (gpt-4o, trilingual EN/AR/Roman-Urdu) takes the conversation. When the AI emits a structured `INTENT: confirm_order` block, we create an `Order` and fire a **server-side Purchase event** to Meta CAPI with the stored `fbc`/`fbp` — so the WhatsApp purchase attributes back to the original ad in Meta Ads Manager. **This is the whole point of the product.**

Read `app/services/ai.py` and `app/services/attribution.py` first.

---

## 3 · Auth model (important — don't break this)

There are **two user kinds**, with **one shared login endpoint**:

- **Reseller** — row in `resellers` table, plan = silver/gold/platinum. Everyone who signs up becomes one. Their JWT has `kind="reseller"`.
- **Admin** — exactly one row in `admin_users`. Identified by `settings.admin_email` (env var) — currently `arabiadropshipping05@gmail.com` in prod. Their JWT has `kind="admin"`.

Both authenticate via `POST /auth/login`. The router routes by email match (`_is_protected_admin_email`). See `app/routers/auth.py`.

`ensure_sole_admin()` runs on startup and idempotently makes sure exactly one AdminUser exists with the configured email + a password from `ADMIN_PASSWORD` env. Changing the admin password via the UI is **per-session only** — Render restart will reset it to whatever `ADMIN_PASSWORD` is set to. To change permanently, update the env var.

Auth deps live in `app/deps.py`:
- `get_current_reseller` — for reseller-only endpoints (`/me/*`, `/products`, etc.)
- `get_current_admin` — for admin-only endpoints (`/admin/*`)
- `get_current_user` — union, returns either; used by `/auth/me`

The portal stores the JWT in `localStorage` under `arabia_token` and decides Admin vs Reseller portal entry from the returned `role` field. The portal route layout enforces it — `app/admin/layout.tsx` redirects if `role !== "admin"`.

---

## 4 · Local dev setup (10 min)

### Backend

```bash
cd ~/work/arabia-ecom-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# Get .env from Zubair (it has prod-ish secrets pointing at a shared Render Postgres).
# Or build your own:
cp .env.example .env
# Generate the two crypto secrets:
python -c "from cryptography.fernet import Fernet; print('FERNET_KEY=' + Fernet.generate_key().decode())"
python -c "import secrets; print('JWT_SECRET=' + secrets.token_urlsafe(48))"
# Set DATABASE_URL to your Postgres. Set OPENAI_API_KEY.

alembic upgrade head
python scripts/seed.py          # creates demo reseller(s) + plans

uvicorn app.main:app --reload   # → http://localhost:8000 (Swagger at /docs)
```

⚠ If you `Fernet`-rotate the key, every existing encrypted token in the DB (Meta CAPI, WA access_token, pool tokens) becomes unreadable. Don't rotate without re-encrypting. There's no key rotation script yet — write one if you have to.

### Portal

```bash
cd ~/work/ai-order-portal
npm install
# .env.local needs one line:
echo 'NEXT_PUBLIC_API_BASE=http://localhost:8000' > .env.local
# For talking to the deployed backend instead:
# NEXT_PUBLIC_API_BASE=https://arabia-ecom-api.onrender.com

npm run dev       # → http://localhost:3000
```

### Test users

`scripts/seed.py` creates:
- Admin: `arabiadropshipping05@gmail.com` / password from `ADMIN_PASSWORD` env
- Demo reseller: `demo@example.com` / `demo1234`

Real prod reseller right now is `zubairkhawer@gmail.com` (test account that owns a real WABA + universal pool slot).

---

## 5 · Required env vars

### Backend (`.env`)
| Key | Notes |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg://...` — driver MUST be `psycopg` (v3), the URL is normalized in `app/db.py` if you forget |
| `JWT_SECRET` | Any long random string |
| `JWT_EXPIRES_MINUTES` | Default `10080` (7 days) |
| `FERNET_KEY` | Generated as above. **Never rotate without migration.** |
| `OPENAI_API_KEY` | Required for the AI bot. Without it the bot returns a dev-stub reply. |
| `OPENAI_MODEL` | Default `gpt-4o` |
| `META_GRAPH_VERSION` | Default `v21.0` |
| `WA_VERIFY_TOKEN` | The shared secret you typed into Meta's webhook config |
| `ADMIN_EMAIL` | Sole-admin email (prod: `arabiadropshipping05@gmail.com`) |
| `ADMIN_PASSWORD` | Sole-admin login password — `ensure_sole_admin()` enforces this on startup |
| `APP_BASE_URL` | Backend's own public URL (e.g. `https://arabia-ecom-api.onrender.com`) |
| `FRONTEND_BASE_URL` / `LINK_DOMAIN` | Portal URL — used for redirect link construction |

### Portal (`.env.local`)
| Key | Notes |
|---|---|
| `NEXT_PUBLIC_API_BASE` | Backend URL — e.g. `https://arabia-ecom-api.onrender.com` |

Vercel project env vars live under: **Vercel dashboard → arabia-ecom project → Settings → Environment Variables**.

Render env vars live under: **Render dashboard → arabia-ecom-api → Environment**.

---

## 6 · Repo map — where to look for X

### Backend (`app/`)

```
app/
├── main.py                  ← FastAPI app, routers wired here, ensure_sole_admin() startup
├── config.py                ← pydantic-settings, reads .env
├── db.py                    ← engine + SessionLocal + URL normalization
├── deps.py                  ← get_current_reseller / get_current_admin / get_current_user
├── security.py              ← bcrypt + JWT issue/decode + Fernet encrypt/decrypt
├── models/                  ← SQLAlchemy 2.0 models (one file per table family)
│   ├── reseller.py          ← Reseller + AISetting
│   ├── meta_config.py       ← MetaConfig (Pixel ID + CAPI token enc + verify state)
│   ├── whatsapp_config.py   ← WhatsAppConfig (own-number creds OR pool selection)
│   ├── pool.py              ← PoolNumber + PoolAssignment
│   ├── product.py           ← Product + Option + Variant + Bundle
│   ├── customer.py / order.py / chat.py / click.py / template.py / notification.py
│   ├── billing.py / shopify.py / platform_settings.py
│   ├── admin.py             ← AdminUser
│   └── _base.py             ← IdMixin (32-char nanoid), TimestampMixin
├── routers/                 ← one file per /prefix
│   ├── auth.py              ← /auth/signup, /login, /me, /password
│   ├── resellers.py         ← /me/* (profile, ai-settings, meta-config, wa-config)
│   ├── products.py          ← CRUD + pricing engine quote
│   ├── links.py             ← /links/resolve/{slug}, /links/click
│   ├── webhooks.py          ← Meta WhatsApp webhook (per-reseller AND pool)
│   ├── chats.py             ← list/get/mode/human-reply
│   ├── orders.py            ← CRUD, follow-up send, CSV
│   ├── tracking.py          ← /tracking/overview (multi-platform dashboard)
│   ├── admin.py             ← /admin/* (cross-reseller, pool numbers, cleanup)
│   ├── billing.py           ← plan tiers / usage
│   └── shopify.py           ← per-reseller store CRUD + sync
├── services/                ← business logic that doesn't belong on a router
│   ├── ai.py                ← OpenAI agent: prompt builder, language detect, INTENT parser
│   ├── attribution.py       ← Meta CAPI dispatch (AddToCart, Purchase, test events)
│   ├── whatsapp_cloud.py    ← Meta Cloud API client (verify_creds, send_text, send_template)
│   ├── wa_credentials.py    ← Resolves the right (pnid, token) for an outbound message
│   ├── pool_router.py       ← get_or_assign(reseller) — picks a pool number with capacity
│   ├── pricing.py           ← variant → bundle → discount stacking
│   ├── shopify_sync.py      ← Shopify Admin API client + product sync
│   └── cleanup.py           ← hard_delete_reseller() — FK-safe cascade
├── schemas/                 ← Pydantic request/response models
└── tests/                   ← pytest — pricing tests are pure unit, others need DB
alembic/
├── env.py                   ← reads DATABASE_URL via config.settings
└── versions/                ← one migration per change. Generate via `alembic revision --autogenerate`
scripts/
├── seed.py                  ← demo data
└── (none others)
```

### Portal (`app/`)

```
app/
├── (auth)/login/page.tsx        ← shared login (admin or reseller)
├── (auth)/signup/page.tsx       ← reseller signup
├── admin/
│   ├── layout.tsx               ← role guard: redirect if !admin
│   ├── page.tsx                 ← admin dashboard (cross-reseller stats)
│   ├── resellers/page.tsx       ← list + per-reseller detail
│   ├── pool-numbers/page.tsx    ← manage universal pool: add / disable / view webhook URL
│   ├── settings/page.tsx        ← PlatformSettings (defaults + caps + video URLs)
│   ├── chats/, orders/, tracking/, billing/  ← cross-reseller views
│   └── notifications/page.tsx
├── reseller/
│   ├── layout.tsx               ← role guard: redirect if !reseller
│   ├── page.tsx                 ← dashboard (stats + recent chats + onboarding progress)
│   ├── setup/page.tsx           ← Channel Setup wizard: WhatsApp + Meta + Shopify
│   ├── settings/page.tsx        ← AI bot settings (3 tabs: General / Business Hours / Profile)
│   ├── products/, orders/, chats/, analytics/page.tsx
├── r/[slug]/page.tsx            ← the public redirect page that fires the Pixel
├── api/                         ← (almost empty — almost everything proxies to backend)
└── layout.tsx                   ← root layout, font, providers

components/
├── layout/Shell.tsx             ← The chrome (sidebar + topbar) used by every admin/reseller page
├── ui/                          ← Card, Tabs, Input, Select, Toggle, Button, CopyField, Badge
├── setup/                       ← WhatsAppTutorial, MetaTutorial, ShopifyTutorial (collapsible help)
└── brand/BrandLogos.tsx         ← WhatsApp/Meta/Shopify SVG marks

lib/
├── api.ts                       ← fetch wrapper. Reads JWT from localStorage, throws ApiError
├── role.ts                      ← useRole() — reads profile from /auth/me, memo'd
├── types.ts                     ← all shared backend types mirrored to TS
└── cn.ts                        ← clsx + tailwind-merge
```

---

## 7 · Critical flows — bookmarks

### Sign-up to first AI reply

1. `POST /auth/signup` → creates Reseller + AISetting (seeded from `PlatformSettings.default_*`)
2. `POST /me/wa-config` with `number_type=universal` → eagerly assigns a PoolNumber (NEW behaviour, see §10), or `number_type=own` with creds → verifies against Meta and saves
3. `POST /me/meta-config` with `pixel_id` + `capi_access_token` → saves; `POST /me/meta-config/verify` fires a test InitiateCheckout to confirm
4. `POST /me/shopify/stores` → connect Shopify; `POST /me/shopify/stores/{id}/sync` → pulls products
5. Reseller copies their product link `https://arabia-ecom.vercel.app/r/{slug}`, runs an ad
6. Customer clicks → portal `/r/{slug}` page calls `GET /links/resolve/{slug}` → fires Pixel + `POST /links/click` → redirects to `wa.me/...[c_xxxxxxxx]`
7. Customer sends message → Meta calls `POST /webhooks/wa/{reseller_id}` (or `/webhooks/wa/pool/{pool_number_id}`)
8. Webhook parses `[c_xxx]` → matches ClickSession → AI replies
9. AI emits `INTENT: confirm_order` → backend creates Order → fires Purchase CAPI

### AI prompt construction (`services/ai.py`)

`build_system_prompt(reseller, ai, catalogue, language)` returns the system message. The reseller-tunable inputs that affect it are `AISetting.ai_name`, `tone`, `convince_hesitant`, and the new `opening_message` (translated by the AI into the customer's detected language for the first reply).

Language detection is heuristic (`detect_language`) — Arabic codepoint range hits return `arabic`; ≥2 Roman-Urdu marker tokens return `roman_urdu`; otherwise `english`. The prompt instructs the LLM to *always* reply in the same language the customer just used.

`heuristic_wants_human(text)` catches "real agent" requests in 3 languages and flips the chat to `pending_human` even if the LLM doesn't emit the escalate intent.

### Outbound WhatsApp credentials resolution (`services/wa_credentials.py`)

Given a Chat, returns the right `(phone_number_id, access_token_enc)` to use. If the chat's `click_session` has a `pool_number_id`, we use that PoolNumber's creds. Otherwise we use the reseller's own `WhatsAppConfig`. This is why a reseller can connect both an "own" number and have customers also message a pool number that's bound to their `ref_token`.

---

## 8 · Universal pool — how it works

A reseller selects `number_type=universal` in Channel Setup. As of the last commit, we **eagerly** assign them a slot in `pool_router.get_or_assign(db, reseller)` at save-time (was previously lazy on first link click). If no pool number is available for their country, the API returns **503** with a clear message — the admin needs to add a PoolNumber first.

**Pool numbers are managed in the admin portal** at `/admin/pool-numbers`. Adding one requires:
- Display number in E.164 (e.g. `+971 52 866 6592`)
- `country` (free text like "United Arab Emirates") + `country_code` (matches `Reseller.country`, e.g. `UAE`)
- `waba_id`, `phone_number_id`, `access_token` (System User permanent token from Meta)
- `capacity` (default 50)

The webhook for a pool number is `https://arabia-ecom-api.onrender.com/webhooks/wa/pool/{pool_number_id}` — register this in **Meta WhatsApp Manager → Configuration → Webhooks** for that WABA. Subscribe to `messages`.

Inbound message routing: the customer's text contains `[c_xxxxxxxx]` (the `ref_token`). We look up the ClickSession, get its `reseller_id`, attach the chat to that reseller. That's how one pool number serves many resellers — each click is bound to a specific reseller via the ref_token.

**Currently in prod**: 1 PoolNumber exists — `+971 52 866 6592`, WABA `1255621313361811`, PNID `1256642720855224`, status active, 1/50 assigned (to `zubairkhawer@gmail.com`). Token is a SYSTEM_USER permanent token, never expires.

---

## 9 · Meta Pixel + CAPI — operational notes

The reseller's MetaConfig holds three things:
- `pixel_id` — also accepts a Dataset ID
- `capi_access_token_enc` — Fernet-encrypted CAPI token
- `test_event_code` — optional `TEST…` code for routing to Meta's Test Events viewer

`POST /me/meta-config/verify` posts a synthetic InitiateCheckout to `https://graph.facebook.com/{ver}/{pixel_id}/events`. On 2xx, we flip `is_capi_verified=True`.

The Purchase event (the one that matters for ad attribution) fires from `services/attribution.py` when `Order.status` transitions to `confirmed`. It uses the customer's `fbp` / `fbc` / `fbclid` from the original ClickSession. **If the ClickSession is missing those, Meta sees the event but can't attribute it back** — that's by design, but worth knowing if attribution looks broken.

---

## 10 · Last 30 commits — what was just shipped (not yet in main as of this handoff commit)

The current uncommitted change-set covers two product asks:

1. **Reseller Settings simplified** — Personality + Language tabs removed. AI settings reduced to 3 fields: AI Name, Opening Message, Reply Length. New `opening_message` column on `AISetting` (migration `8a16de97e867`), seeded at signup from `PlatformSettings.default_opening_message` with `{{brand}}` → reseller name. The opening message is injected into the AI prompt as the first-contact greeting.

2. **Onboarding tiles → links** — Reseller dashboard's "Onboarding Progress" tiles are now clickable, mapped to the right setup page each (`page.tsx`'s `ONBOARDING_HREFS` map).

3. **Eager pool assignment** — universal-pool resellers now get a PoolNumber assigned at save-time, not on first link click. UI shows the assigned number immediately. Removed the "Pool capacity: 50 resellers per number" row from the reseller-facing UI (that's internal info).

Other recent (already-pushed) changes:
- WhatsApp connected-state card with disconnect button + enforcement that only one of {own, universal} can be active
- Restored AI Bot tabs in reseller Settings (then later trimmed — see #1 above)
- Admin platform settings page (singleton row in `platform_settings`)
- Notification bell with audible ping when unread count rises
- Per-store Shopify connection with sync progress
- Pool number management page in admin shows the per-number webhook URL

---

## 11 · Recipes — common ops

### Add a pool number from the CLI (when admin UI is fiddly)
```python
from app.db import SessionLocal
from app.models import PoolNumber
from app.security import encrypt

db = SessionLocal()
db.add(PoolNumber(
    number="+971 50 123 0001",
    country="United Arab Emirates",
    country_code="UAE",
    flag="🇦🇪",
    capacity=50,
    waba_id="...",
    phone_number_id="...",
    access_token_enc=encrypt("EAA..."),  # SYSTEM_USER permanent token
    status="active",
))
db.commit()
```

### Verify a Meta token works end-to-end
```python
import asyncio
from app.services.whatsapp_cloud import verify_creds
asyncio.run(verify_creds("PHONE_NUMBER_ID", "EAA..."))
# Returns {ok: True, status: 200, body: "...display_phone_number..."} when good
```

### Inspect a token's scopes (uses Meta's debug_token endpoint)
```bash
curl "https://graph.facebook.com/v21.0/debug_token?input_token=$TOK" -H "Authorization: Bearer $TOK" | jq
```
SYSTEM_USER tokens never expire (`expires_at: 0`). The two scopes you want are `whatsapp_business_management` + `whatsapp_business_messaging`.

### Hard-delete a reseller (FK-safe, cleans chat/orders/clicks/pool assignment)
```python
from app.db import SessionLocal
from app.services.cleanup import hard_delete_reseller
db = SessionLocal()
hard_delete_reseller(db, reseller_id="...")
db.commit()
```

### Reset universal-pool reseller's number
```python
# In python REPL
from app.db import SessionLocal
from app.models import PoolAssignment, PoolNumber
from sqlalchemy import select

db = SessionLocal()
a = db.execute(select(PoolAssignment).where(PoolAssignment.reseller_id == "...")).scalar_one_or_none()
if a:
    pn = db.get(PoolNumber, a.pool_number_id)
    if pn and pn.assigned > 0: pn.assigned -= 1
    db.delete(a)
db.commit()
# Next call to get_or_assign() will give them a fresh slot
```

### Run a migration
```bash
# After editing a model:
alembic revision --autogenerate -m "short description"
# Review the generated file in alembic/versions/ — autogen is dumb about JSON defaults and indexes
alembic upgrade head
# On prod: just push to main, Render runs `alembic upgrade head` in build.
```

### Tail prod logs
- **Render**: dashboard → arabia-ecom-api → Logs tab (live tail). Filter by `webhook` to see WhatsApp events arriving.
- **Vercel**: dashboard → arabia-ecom → Deployments → click latest → Runtime logs.

---

## 12 · Gotchas (read before you debug for an hour)

- **psycopg v3 required** — `app/db.py` rewrites `postgres://` → `postgresql+psycopg://` on startup. Don't add psycopg2.
- **Render free tier sleeps** — first request after 15 min idle takes 30s to cold-start. The portal's `api.ts` waits up to 60s.
- **JWT `kind` matters more than `role`** — `kind` is in the token claims and decides which deps fire. `role` on `ResellerOut` is a UI hint.
- **PlatformSettings is a singleton** — read it with `db.execute(select(PlatformSettings)).scalar_one_or_none()`. If row missing, `ensure_platform_settings()` (in `main.py` startup) creates it with defaults.
- **The reseller's `role` is forced to "reseller" on every login** even if a legacy row says `admin`. Admin is exclusively in `admin_users`. See `auth.py:80`.
- **`alembic revision --autogenerate` is not lossless** — review the diff. It tends to drop `JSON` default callables and gets `unique=True` wrong on multi-column indexes.
- **Don't rotate `FERNET_KEY`** without re-encrypting all `*_enc` columns. There's no rotation script.
- **`OPENAI_API_KEY` unset → dev stub reply** ("Thanks for your message! I'll get back to you shortly. (dev stub)"). Useful for local but obviously wrong in prod.
- **Meta webhook signing** — `WA_VERIFY_TOKEN` env must match the value you typed into Meta's webhook configuration. Mismatch → handshake fails silently.
- **The reseller's `country` field** drives pool number routing — `PoolNumber.country_code` must match (`UAE`, `KSA`, `PAK`). If a reseller signs up with country `AE` instead of `UAE`, they won't find any pool number.
- **Encrypted columns end in `_enc`** — `meta_config.capi_access_token_enc`, `whatsapp_config.access_token_enc`, `pool_number.access_token_enc`. Always go through `security.encrypt()` / `decrypt()`.

---

## 13 · Pending / known issues

- **No pool numbers for non-UAE countries** in prod yet. KSA, PAK, etc. resellers using universal will hit a 503 until admin adds one. Same for `country != UAE`.
- **Phase 1.5 attribution** — TikTok / Snap / Google CAPI dispatchers are stubs in `services/attribution.py`. Only Meta is wired end-to-end.
- **Template approval flow** — admin can mock-approve templates, but real Meta template submission API isn't wired.
- **No subscription billing** — `Reseller.plan` is a string field. No Stripe/Paddle integration.
- **No rotation script for `FERNET_KEY`** — write one if you ever need to rotate.
- **Onboarding email** — there's no welcome email after signup. Resellers get an in-app onboarding card only.

---

## 14 · Claude Code session parity

The previous engineer worked this codebase with Claude Code (Anthropic's official CLI). To get an identical session experience:

### Install + model
1. Install Claude Code: `curl -fsSL https://claude.ai/install.sh | bash` (or via the desktop app at `claude.ai/code`).
2. Authenticate with an Anthropic account that has access to **Claude Opus 4.7 (1M context)** — the model used in this project. The 1M context window matters here because the project spans two repos.
3. Run `claude` from inside `arabia-ecom-api/` for backend work, or from `ai-order-portal/` for frontend work.

### Auto-loaded project context
Both repos already contain a `CLAUDE.md` at the root. Claude Code loads it automatically at session start, so your colleague will get the same:
- Architecture summary
- Pointer to **this file** (`HANDOFF.md`) as the single source of truth
- The "don't do these" rules (don't rotate FERNET_KEY, don't drop `_enc` columns, etc.)

No additional setup is needed — `CLAUDE.md` is committed.

### Personal memory (not shared)
Claude Code's `/memory` system is per-user, stored at `~/.claude/projects/<repo-hash>/memory/`. It can't be cloned between machines and shouldn't be — it accumulates personal context (your role, your editing preferences, feedback you've given). Your colleague's memory will build up naturally as they work.

The **project facts** that previously lived in Zubair's memory (architecture, file paths, prod URLs, attribution flow, gotchas) are all in **`HANDOFF.md`** and the two `CLAUDE.md` files. Memory was never the source of truth for project facts.

### Workflow conventions to keep
- **Conventional commit messages** (look at `git log --oneline -20` for the style — sentence-case subject, focused on the *why* in the body)
- **Two repos = two commits.** Don't bundle a backend + portal change into one repo's commit; split it.
- **Push to `main` deploys to prod.** No staging environment. So: small commits, frequent pushes, and test locally first.
- **No `--no-verify` ever.** Hooks exist for a reason; fix the underlying issue.

### Tools the previous engineer used in-session
- Manual DB inspection via `python` REPL with `from app.db import SessionLocal` (recipes in §11)
- Meta API testing via inline `httpx` scripts (also in §11)
- Render Logs tab (live tail, filterable by request path) for prod debugging
- Vercel Runtime Logs for portal-side issues

---

## 15 · Communication

- **Slack**: ask Zubair for the workspace invite.
- **Linear/Issues**: not yet wired — Safdar prefers WhatsApp screenshots, which is hilariously on-brand. Zubair maintains a private notion doc with the backlog.
- **The client (Safdar)** prefers product-shaped explanations over technical ones. When he says "the AI is being weird", check `Chat.mode` (might be `pending_human`) and the most recent `Message.ai_intent` (might be a stuck escalate).

When something breaks in prod and you need to ship a fix fast: the deploy pipeline is fast (~2 min on Render, ~30s on Vercel), so prefer small + frequent commits over batching.

Good luck.
