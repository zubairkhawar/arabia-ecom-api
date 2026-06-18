def test_signup_and_login(client):
    import secrets
    email = f"flow+{secrets.token_hex(4)}@example.com"
    r = client.post("/auth/signup", json={
        "name": "Flow Tester", "email": email, "password": "supersecret",
        "country": "UAE", "currency": "AED",
    })
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    assert token

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == email

    r2 = client.post("/auth/login", json={"email": email, "password": "supersecret"})
    assert r2.status_code == 200


def test_login_wrong_password_fails(client, reseller):
    r = client.post("/auth/login", json={"email": reseller.email, "password": "WRONG"})
    assert r.status_code == 401


def test_protected_route_requires_token(client):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/products").status_code == 401


def test_signup_duplicate_email_409(client, reseller):
    r = client.post("/auth/signup", json={
        "name": "x", "email": reseller.email, "password": "x", "country": "UAE", "currency": "AED",
    })
    assert r.status_code == 409
