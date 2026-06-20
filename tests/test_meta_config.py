def test_meta_config_defaults_when_unset(client, auth):
    r = client.get("/me/meta-config", headers=auth).json()
    assert r["pixel_id"] is None
    assert r["has_token"] is False
    assert r["default_event"] == "InitiateCheckout"
    assert r["action_source"] == "website"
    assert r["is_capi_verified"] is False


def test_meta_config_put_and_persists_new_fields(client, auth):
    r = client.put("/me/meta-config", headers=auth, json={
        "pixel_id": "123456789",
        "capi_access_token": "FAKE-TOKEN",
        "test_event_code": "TEST7",
        "default_event": "AddToCart",
        "action_source": "business_messaging",
    }).json()
    assert r["pixel_id"] == "123456789"
    assert r["has_token"] is True
    assert r["default_event"] == "AddToCart"
    assert r["action_source"] == "business_messaging"


def test_meta_config_rejects_invalid_event(client, auth):
    r = client.put("/me/meta-config", headers=auth, json={"default_event": "Teleport"})
    assert r.status_code == 422


def test_meta_config_rejects_invalid_action_source(client, auth):
    r = client.put("/me/meta-config", headers=auth, json={"action_source": "carrier_pigeon"})
    assert r.status_code == 422


def test_meta_config_verify_requires_token(client, auth):
    # No token set yet
    r = client.post("/me/meta-config/verify", headers=auth)
    assert r.status_code == 400


def test_meta_config_verify_hits_meta_with_fake_token(client, auth):
    client.put("/me/meta-config", headers=auth, json={
        "pixel_id": "123456789", "capi_access_token": "FAKE-TOKEN",
    })
    r = client.post("/me/meta-config/verify", headers=auth)
    assert r.status_code == 200
    body = r.json()
    # Meta will reject the fake token with 400 — that proves we actually
    # called the API. ok=False, verified=False, status=400 expected.
    assert body["ok"] is False
    assert body["capi_status"] == 400
    assert body["verified"] is False


def test_meta_config_delete(client, auth):
    client.put("/me/meta-config", headers=auth, json={
        "pixel_id": "x", "capi_access_token": "y",
    })
    r = client.delete("/me/meta-config", headers=auth)
    assert r.status_code == 204
    got = client.get("/me/meta-config", headers=auth).json()
    assert got["pixel_id"] is None
    assert got["has_token"] is False
