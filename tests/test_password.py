def test_change_password_happy_path(client, reseller):
    # initial login works with the fixture password
    r = client.post("/auth/login", json={"email": reseller.email, "password": "secret"})
    assert r.status_code == 200
    tok = r.json()["access_token"]

    # change it
    resp = client.post(
        "/auth/password",
        headers={"Authorization": f"Bearer {tok}"},
        json={"old_password": "secret", "new_password": "BrandNew1!"},
    )
    assert resp.status_code == 204

    # old password no longer works
    r = client.post("/auth/login", json={"email": reseller.email, "password": "secret"})
    assert r.status_code == 401

    # new password works
    r = client.post("/auth/login", json={"email": reseller.email, "password": "BrandNew1!"})
    assert r.status_code == 200


def test_change_password_wrong_old_rejected(client, reseller, token):
    r = client.post(
        "/auth/password",
        headers={"Authorization": f"Bearer {token}"},
        json={"old_password": "WRONG", "new_password": "Whatever1!"},
    )
    assert r.status_code == 401


def test_change_password_too_short_rejected(client, reseller, token):
    r = client.post(
        "/auth/password",
        headers={"Authorization": f"Bearer {token}"},
        json={"old_password": "secret", "new_password": "short"},
    )
    assert r.status_code == 422


def test_change_password_same_as_old_rejected(client, reseller, token):
    r = client.post(
        "/auth/password",
        headers={"Authorization": f"Bearer {token}"},
        json={"old_password": "secret", "new_password": "secret"},
    )
    assert r.status_code == 422


def test_change_password_requires_auth(client):
    r = client.post("/auth/password", json={"old_password": "x", "new_password": "Whatever1!"})
    assert r.status_code == 401


def test_admin_can_change_password(client, admin_user, admin_token):
    """Admin token works too (since the password endpoint uses get_current_user)."""
    # The admin_user fixture creates with hash_password('secret')
    r = client.post(
        "/auth/password",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"old_password": "secret", "new_password": "AdminNew1!"},
    )
    assert r.status_code == 204
