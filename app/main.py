from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .config import settings
from .db import engine
from .routers import (
    auth, resellers, products, links, webhooks, chats,
    orders, templates, tracking, admin, billing,
)


app = FastAPI(
    title="Arabia Ecom API",
    version="1.0.0",
    description="WhatsApp + Shopify AI order orchestration backend.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_base_url,
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:4321",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["meta"])
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "status": "ok",
        "env": settings.app_env,
        "db": db_ok,
        "openai_configured": bool(settings.openai_api_key),
    }


app.include_router(auth.router)
app.include_router(resellers.router)
app.include_router(products.router)
app.include_router(links.router)
app.include_router(webhooks.router)
app.include_router(chats.router)
app.include_router(orders.router)
app.include_router(templates.router)
app.include_router(tracking.router)
app.include_router(admin.router)
app.include_router(billing.router)
