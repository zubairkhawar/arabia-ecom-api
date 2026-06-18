def test_create_and_confirm_order(client, auth, reseller):
    p = client.post("/products", headers=auth, json={
        "name": "Watch", "price": 300, "currency": "AED", "country": "UAE", "channels": ["whatsapp"],
    }).json()
    r = client.post("/orders", headers=auth, json={
        "customer_phone": "971500000010",
        "customer_name": "Buyer",
        "items": [{"product_id": p["id"], "qty": 2}],
        "address": "Dubai Marina",
        "confirm": True,
    })
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["amount"] == 600
    assert order["status"] == "confirmed"
    assert order["purchase_event_sent"] is True


def test_update_delivery_status(client, auth, reseller):
    p = client.post("/products", headers=auth, json={
        "name": "Lamp", "price": 50, "currency": "AED", "country": "UAE", "channels": ["whatsapp"],
    }).json()
    o = client.post("/orders", headers=auth, json={
        "customer_phone": "971500000020", "items": [{"product_id": p["id"], "qty": 1}],
    }).json()
    upd = client.patch(f"/orders/{o['id']}", headers=auth, json={
        "delivery_status": "in_transit", "tracking_number": "TR-12345",
    }).json()
    assert upd["delivery_status"] == "in_transit"
    assert upd["tracking_number"] == "TR-12345"


def test_invalid_delivery_status_rejected(client, auth, reseller):
    p = client.post("/products", headers=auth, json={
        "name": "X", "price": 10, "currency": "AED", "country": "UAE", "channels": ["whatsapp"],
    }).json()
    o = client.post("/orders", headers=auth, json={
        "customer_phone": "971500000030", "items": [{"product_id": p["id"], "qty": 1}],
    }).json()
    r = client.patch(f"/orders/{o['id']}", headers=auth, json={"delivery_status": "teleported"})
    assert r.status_code == 422


def test_csv_export_includes_header_and_row(client, auth, reseller):
    p = client.post("/products", headers=auth, json={
        "name": "P", "price": 99, "currency": "AED", "country": "UAE", "channels": ["whatsapp"],
    }).json()
    client.post("/orders", headers=auth, json={
        "customer_phone": "971500000040", "items": [{"product_id": p["id"], "qty": 1}],
    })
    r = client.get("/orders/export/csv", headers=auth)
    assert r.status_code == 200
    body = r.text
    assert "order_id,created_at,status,delivery_status" in body
    assert "971500000040" in body


def test_csv_import_updates_tracking(client, auth, reseller):
    p = client.post("/products", headers=auth, json={
        "name": "P", "price": 99, "currency": "AED", "country": "UAE", "channels": ["whatsapp"],
    }).json()
    o = client.post("/orders", headers=auth, json={
        "customer_phone": "971500000050", "items": [{"product_id": p["id"], "qty": 1}],
    }).json()
    csv = f"order_id,tracking_number,delivery_status\n{o['code']},TR-9999,dispatched\nBOGUS-9999,Z,delivered\n"
    r = client.post(
        "/orders/import/csv", headers=auth,
        files={"file": ("orders.csv", csv, "text/csv")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] == 1
    assert body["not_found"] == 1
    updated = client.get(f"/orders/{o['id']}", headers=auth).json()
    assert updated["tracking_number"] == "TR-9999"
    assert updated["delivery_status"] == "dispatched"
