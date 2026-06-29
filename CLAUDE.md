# Arabia Ecom — Backend (FastAPI)

> **Read `HANDOFF.md` first.** It's the single source of truth for this codebase and the sister portal repo (`zubairkhawar/arabiaEcom`). Architecture, env vars, auth model, attribution flow, deploy targets, recipes, and gotchas are all there. Don't open code before reading it.

## Project at a glance

Multi-tenant WhatsApp + Shopify AI order-orchestration backend.

- **Stack:** FastAPI · SQLAlchemy 2.0 · Alembic · Pydantic v2 · psycopg v3 · OpenAI gpt-4o · Meta WhatsApp Cloud API · Fernet-encrypted secrets
- **Database:** PostgreSQL on Render (Singapore region)
- **Deploy:** push to `main` → Render auto-builds (`alembic upgrade head` runs in build) → restart uvicorn
- **Sister repo:** `zubairkhawar/arabiaEcom` — Next.js portal (Admin + Reseller UIs), hosted on Vercel

## Codebase rules

- **Always edit existing files; don't add new ones unless `HANDOFF.md` says where new files belong.**
- **No comments** beyond what's already in the file unless a non-obvious WHY needs documenting (a hidden constraint, a workaround, a Meta API quirk).
- **No backwards-compatibility shims.** If you change a schema or column, change every caller in the same commit.
- **Never rotate `FERNET_KEY`** without re-encrypting all `*_enc` columns. There is no rotation script.
- **psycopg v3 only** — `app/db.py` normalizes the URL on startup. Don't add psycopg2.
- **Admin is a singleton** — the email in `settings.admin_email` is the only admin. Don't add admin-creation endpoints.
- **Migrations:** `alembic revision --autogenerate -m "..."` then review the diff before applying. Autogen often loses JSON default callables.
- **Tests:** `pytest` — pricing tests are pure unit (no DB), the rest need a Postgres `DATABASE_URL` and clean up after themselves.

## Mental model for new tasks

When a user asks you to change something, identify which of these flows it touches:

1. **Signup → AI bot live** (`auth.py` → `resellers.py` → `services/ai.py` → webhook)
2. **Pixel attribution** (`/r/{slug}` portal page → `/links/click` → CAPI dispatcher in `services/attribution.py`)
3. **Pool routing** (`pool_router.get_or_assign` → `wa_credentials.resolve_outbound` → webhook routing)
4. **AI prompt** (`services/ai.py:build_system_prompt` — the thing that translates `AISetting` + catalogue into the system message)
5. **Order lifecycle** (Chat → AI INTENT → Order created → `confirmed` triggers Purchase CAPI)

Most user asks land in exactly one of these. Match it to the flow first, then to the file.

## Don't do these without asking

- Push to `main` while a real ad campaign is running (Safdar runs ads — coordinate)
- Touch a PoolNumber row that has live `assigned > 0` (resellers will lose their inbound route)
- Change `Reseller.country` codes (`UAE`/`KSA`/`PAK`) — pool routing keys off them
- Drop or rename columns ending in `_enc` (Fernet-encrypted, can't be reconstructed)
- Add a new admin email or change the sole-admin policy

When in doubt: ask Zubair first.
