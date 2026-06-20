def test_deeplink_uses_ref_format(client, auth, pool_uae):
    p = client.post("/products", headers=auth, json={
        "name": "X", "price": 10, "currency": "AED", "country": "UAE", "channels": ["whatsapp"],
    }).json()
    r = client.get(f"/links/resolve/{p['slug']}").json()
    # URL-encoded [ref:c_xxx]
    assert "%5Bref%3Ac_" in r["wa_deeplink"]


def test_click_captures_utm_content_and_term(client, auth, pool_uae, db):
    p = client.post("/products", headers=auth, json={
        "name": "X", "price": 10, "currency": "AED", "country": "UAE", "channels": ["whatsapp"],
    }).json()
    r = client.post("/links/click", json={
        "slug": p["slug"],
        "utm_content": "ad-creative-7",
        "utm_term": "earbuds",
        "landing_url": "https://example.com/r/X",
    }).json()
    from app.models import ClickSession
    cs = db.query(ClickSession).filter(ClickSession.id == r["click_session_id"]).first()
    assert cs.utm_content == "ad-creative-7"
    assert cs.utm_term == "earbuds"
    assert cs.landing_url == "https://example.com/r/X"


def test_click_records_wa_number(client, auth, pool_uae, db):
    p = client.post("/products", headers=auth, json={
        "name": "X", "price": 10, "currency": "AED", "country": "UAE", "channels": ["whatsapp"],
    }).json()
    r = client.post("/links/click", json={"slug": p["slug"]}).json()
    from app.models import ClickSession
    cs = db.query(ClickSession).filter(ClickSession.id == r["click_session_id"]).first()
    assert cs.wa_number  # populated from pool or own number


def test_bot_user_agent_does_not_create_click_session(client, auth, pool_uae, db):
    p = client.post("/products", headers=auth, json={
        "name": "X", "price": 10, "currency": "AED", "country": "UAE", "channels": ["whatsapp"],
    }).json()
    before = db.query(__import__("app.models", fromlist=["ClickSession"]).ClickSession).count()
    r = client.post(
        "/links/click",
        headers={"User-Agent": "facebookexternalhit/1.1"},
        json={"slug": p["slug"]},
    )
    body = r.json()
    assert body["bot"] is True
    assert body["click_session_id"] == ""
    after = db.query(__import__("app.models", fromlist=["ClickSession"]).ClickSession).count()
    assert after == before  # no row created


def test_pixel_fired_beacon_sets_flag(client, auth, pool_uae, db):
    p = client.post("/products", headers=auth, json={
        "name": "X", "price": 10, "currency": "AED", "country": "UAE", "channels": ["whatsapp"],
    }).json()
    click = client.post("/links/click", json={"slug": p["slug"], "src_platform": "meta"}).json()
    r = client.post("/links/pixel-fired", json={"click_session_id": click["click_session_id"]})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    from app.models import ClickSession
    cs = db.query(ClickSession).filter(ClickSession.id == click["click_session_id"]).first()
    assert cs.add_to_cart_sent is True


def test_webhook_matches_new_ref_format(client, auth, reseller, pool_uae):
    p = client.post("/products", headers=auth, json={
        "name": "X", "price": 10, "currency": "AED", "country": "UAE", "channels": ["whatsapp"],
    }).json()
    click = client.post("/links/click", json={"slug": p["slug"], "src_platform": "meta"}).json()
    ref = click["ref_token"]
    # Use the spec's [ref:c_xxx] format
    r = client.post(
        f"/webhooks/wa/{reseller.id}",
        json={
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {
                "messaging_product": "whatsapp",
                "contacts": [{"profile": {"name": "Z"}, "wa_id": "971500009000"}],
                "messages": [{
                    "from": "971500009000", "id": "wamid.ref", "type": "text",
                    "text": {"body": f"Hi I want this 🛍️\n[ref:{ref}]"},
                }],
            }}]}],
        },
    )
    assert r.status_code == 200
    assert r.json()["processed"] == 1
    chats = client.get("/chats", headers=auth).json()
    chat_id = next(c["id"] for c in chats if c["customer_phone"] == "971500009000")
    detail = client.get(f"/chats/{chat_id}", headers=auth).json()
    assert detail["click_session_id"] == click["click_session_id"]
    assert detail["src_platform"] == "meta"


def test_meta_config_default_event_drives_attribution_event_name(client, auth, pool_uae, db):
    client.put("/me/meta-config", headers=auth, json={
        "pixel_id": "x", "capi_access_token": "y", "default_event": "AddToCart",
    })
    p = client.post("/products", headers=auth, json={
        "name": "X", "price": 10, "currency": "AED", "country": "UAE", "channels": ["whatsapp"],
    }).json()
    click = client.post("/links/click", json={"slug": p["slug"], "src_platform": "meta"}).json()
    from app.models import AttributionEvent
    evt = db.query(AttributionEvent).filter(AttributionEvent.click_session_id == click["click_session_id"]).first()
    assert evt.event_name == "AddToCart"
