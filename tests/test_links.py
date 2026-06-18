def test_resolve_returns_deeplink_with_ref_token(client, auth, reseller, pool_uae):
    p = client.post("/products", headers=auth, json={
        "name": "Test", "price": 100, "currency": "AED", "country": "UAE",
        "channels": ["whatsapp"],
    }).json()
    r = client.get(f"/links/resolve/{p['slug']}")
    assert r.status_code == 200
    body = r.json()
    assert body["product_id"] == p["id"]
    assert body["wa_target_number"]
    assert "%5Bc_" in body["wa_deeplink"]  # bracket URL-encoded
    assert body["ref_token"] in body["wa_deeplink"]


def test_click_records_session_and_attribution_event(client, auth, reseller, pool_uae, db):
    p = client.post("/products", headers=auth, json={
        "name": "Test", "price": 100, "currency": "AED", "country": "UAE",
        "channels": ["whatsapp"],
    }).json()
    r = client.post("/links/click", json={
        "slug": p["slug"],
        "src_platform": "tiktok",
        "ttclid": "E.C.X.YZ",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ref_token"].startswith("c_")
    # TikTok platform → CAPI is skipped (Phase 1.5)
    assert body["capi_status"] == "skipped"

    from app.models import ClickSession, AttributionEvent
    cs = db.query(ClickSession).filter(ClickSession.id == body["click_session_id"]).first()
    assert cs is not None
    assert cs.src_platform == "tiktok"
    assert cs.ttclid == "E.C.X.YZ"

    evt = db.query(AttributionEvent).filter(AttributionEvent.click_session_id == cs.id).first()
    assert evt is not None
    assert evt.event_name == "AddToCart"
    assert evt.status == "skipped"
