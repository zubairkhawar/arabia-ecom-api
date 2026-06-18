def test_webhook_verify_with_global_token(client, reseller):
    r = client.get(
        f"/webhooks/wa/{reseller.id}",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "arabia-ecom-verify-2026",
            "hub.challenge": "CHALLENGE_X",
        },
    )
    assert r.status_code == 200
    assert r.text == "CHALLENGE_X"


def test_webhook_verify_wrong_token(client, reseller):
    r = client.get(
        f"/webhooks/wa/{reseller.id}",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "X",
        },
    )
    assert r.status_code == 403


def test_inbound_creates_chat_and_matches_attribution(client, auth, reseller, pool_uae, db):
    # Create a product + click_session
    p = client.post("/products", headers=auth, json={
        "name": "Earbuds", "price": 100, "currency": "AED", "country": "UAE",
        "channels": ["whatsapp"],
    }).json()
    click = client.post("/links/click", json={
        "slug": p["slug"], "src_platform": "meta", "fbclid": "X",
    }).json()
    ref = click["ref_token"]

    # Send a synthetic WA webhook payload referencing the ref token
    r = client.post(
        f"/webhooks/wa/{reseller.id}",
        json={
            "object": "whatsapp_business_account",
            "entry": [{"id": "WABA", "changes": [{"value": {
                "messaging_product": "whatsapp",
                "contacts": [{"profile": {"name": "Test Buyer"}, "wa_id": "971500000001"}],
                "messages": [{
                    "from": "971500000001", "id": "wamid.X", "type": "text",
                    "text": {"body": f"Hi! I want the earbuds [{ref}]"},
                }],
            }}]}],
        },
    )
    assert r.status_code == 200
    assert r.json()["processed"] == 1

    # Chat should exist and be tied to the click_session
    chats = client.get("/chats", headers=auth).json()
    assert any(c["customer_phone"] == "971500000001" for c in chats)
    chat_id = next(c["id"] for c in chats if c["customer_phone"] == "971500000001")
    detail = client.get(f"/chats/{chat_id}", headers=auth).json()
    assert detail["src_platform"] == "meta"
    assert detail["click_session_id"] == click["click_session_id"]
    # Two messages: the customer one and the AI dev-stub reply
    assert len(detail["messages"]) >= 2
    senders = [m["sender"] for m in detail["messages"]]
    assert "customer" in senders and "ai" in senders


def test_human_mode_blocks_auto_reply(client, auth, reseller, pool_uae, db):
    p = client.post("/products", headers=auth, json={
        "name": "X", "price": 10, "currency": "AED", "country": "UAE",
        "channels": ["whatsapp"],
    }).json()
    click = client.post("/links/click", json={"slug": p["slug"]}).json()
    ref = click["ref_token"]
    # First message creates chat
    client.post(f"/webhooks/wa/{reseller.id}", json={
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {
            "messaging_product": "whatsapp",
            "contacts": [{"profile": {"name": "B"}, "wa_id": "971500000002"}],
            "messages": [{"from": "971500000002", "id": "1", "type": "text", "text": {"body": f"hi [{ref}]"}}],
        }}]}],
    })
    chat_id = next(c["id"] for c in client.get("/chats", headers=auth).json() if c["customer_phone"] == "971500000002")
    client.post(f"/chats/{chat_id}/mode", headers=auth, json={"mode": "human"})

    # Next inbound should not get an AI reply
    msgs_before = client.get(f"/chats/{chat_id}", headers=auth).json()["messages"]
    client.post(f"/webhooks/wa/{reseller.id}", json={
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {
            "messaging_product": "whatsapp",
            "contacts": [{"profile": {"name": "B"}, "wa_id": "971500000002"}],
            "messages": [{"from": "971500000002", "id": "2", "type": "text", "text": {"body": "follow up"}}],
        }}]}],
    })
    msgs_after = client.get(f"/chats/{chat_id}", headers=auth).json()["messages"]
    # +1 customer message, no AI reply
    assert len(msgs_after) == len(msgs_before) + 1
    assert msgs_after[-1]["sender"] == "customer"
