def _make_product(client, auth):
    return client.post("/products", headers=auth, json={
        "name": "Test Earbuds",
        "description": "Short",
        "main_description": "Long form description",
        "key_points": ["ANC", "24h"],
        "price": 200,
        "currency": "AED",
        "country": "UAE",
        "channels": ["whatsapp"],
        "discount": {"type": "percent", "value": 10},
        "bundles": [{"qty": 2, "price": 350}],
        "options": [{"name": "Color", "values": ["Black", "White"]}],
        "variants": [
            {"label": "Black", "combo": {"Color": "Black"}, "stock": 100},
            {"label": "White", "combo": {"Color": "White"}, "price": 215, "stock": 50},
        ],
    }).json()


def test_create_list_get_product(client, auth):
    p = _make_product(client, auth)
    assert p["slug"]
    assert p["generated_url"].endswith(p["slug"])
    assert len(p["variants"]) == 2
    assert p["discount_type"] == "percent"
    assert p["discount_value"] == 10
    assert len(p["bundles"]) == 1

    lst = client.get("/products", headers=auth).json()
    assert any(x["id"] == p["id"] for x in lst)

    one = client.get(f"/products/{p['id']}", headers=auth).json()
    assert one["id"] == p["id"]


def test_quote_endpoint_matches_pricing_engine(client, auth):
    p = _make_product(client, auth)
    # 2 units: bundle (350) then 10% off = 315
    r = client.post("/products/quote", headers=auth, json={
        "lines": [{"product_id": p["id"], "qty": 2}],
    })
    body = r.json()
    assert body["subtotal"] == 315


def test_update_replaces_options_and_variants(client, auth):
    p = _make_product(client, auth)
    upd = client.patch(f"/products/{p['id']}", headers=auth, json={
        "name": "New name",
        "variants": [{"label": "Only", "combo": {}, "price": 99}],
        "options": [],
    }).json()
    assert upd["name"] == "New name"
    assert len(upd["variants"]) == 1
    assert upd["variants"][0]["price"] == 99


def test_other_reseller_cannot_access(client, reseller, db):
    # Create a product as reseller A
    r = client.post("/auth/login", json={"email": reseller.email, "password": "secret"}).json()
    auth_a = {"Authorization": f"Bearer {r['access_token']}"}
    p = _make_product(client, auth_a)

    # Create reseller B
    import secrets
    other = client.post("/auth/signup", json={
        "name": "B", "email": f"other+{secrets.token_hex(4)}@example.com",
        "password": "x", "country": "UAE", "currency": "AED",
    }).json()
    auth_b = {"Authorization": f"Bearer {other['access_token']}"}

    # Reseller B should NOT see A's product
    g = client.get(f"/products/{p['id']}", headers=auth_b)
    assert g.status_code == 404

    # cleanup the B reseller record
    from tests.conftest import _hard_delete_reseller
    _hard_delete_reseller(db, other["reseller"]["id"])
