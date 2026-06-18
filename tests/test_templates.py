def test_create_template_starts_pending(client, auth):
    r = client.post("/templates", headers=auth, json={
        "name": "Return follow up", "category": "return", "language": "en",
        "body": "Hi {customer_name}, we received your return for order {order_id}.",
    })
    assert r.status_code == 201
    assert r.json()["status"] == "pending"


def test_admin_can_approve_template(client, auth, admin_auth):
    t = client.post("/templates", headers=auth, json={
        "name": "Delivered", "category": "delivered", "language": "en",
        "body": "Your order {order_id} was delivered. How was it?",
    }).json()
    r = client.post(f"/templates/{t['id']}/approval", headers=admin_auth,
                    json={"status": "approved"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    assert r.json()["meta_template_name"]


def test_non_admin_cannot_approve(client, auth):
    t = client.post("/templates", headers=auth, json={
        "name": "X", "body": "x",
    }).json()
    r = client.post(f"/templates/{t['id']}/approval", headers=auth,
                    json={"status": "approved"})
    assert r.status_code == 403


def test_follow_up_blocks_unapproved_template(client, auth, reseller):
    p = client.post("/products", headers=auth, json={
        "name": "X", "price": 10, "currency": "AED", "country": "UAE", "channels": ["whatsapp"],
    }).json()
    o = client.post("/orders", headers=auth, json={
        "customer_phone": "971500000099", "items": [{"product_id": p["id"], "qty": 1}],
    }).json()
    t = client.post("/templates", headers=auth, json={
        "name": "Pending tpl", "body": "x", "language": "en",
    }).json()
    r = client.post(f"/orders/{o['id']}/follow-up", headers=auth, json={"template_id": t["id"]})
    assert r.status_code == 409


def test_editing_approved_template_resets_to_pending(client, auth, admin_auth):
    t = client.post("/templates", headers=auth, json={
        "name": "Edit me", "body": "original body",
    }).json()
    client.post(f"/templates/{t['id']}/approval", headers=admin_auth, json={"status": "approved"})
    upd = client.patch(f"/templates/{t['id']}", headers=auth, json={"body": "totally new body"}).json()
    assert upd["status"] == "pending"
