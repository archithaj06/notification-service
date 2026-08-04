def test_create_notification_happy_path(client):
    resp = client.post(
        "/notifications",
        json={
            "user_id": "user-1",
            "channels": ["email"],
            "priority": "normal",
            "body": "Hello {{name}}",
            "variables": {"name": "Priya"},
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "delivered"
    assert body["channels"][0]["channel"] == "email"
    assert body["channels"][0]["status"] == "delivered"


def test_get_notification_by_id(client):
    create_resp = client.post(
        "/notifications",
        json={"user_id": "user-1", "channels": ["push"], "body": "Hi", "variables": {}},
    )
    notification_id = create_resp.json()["id"]

    get_resp = client.get(f"/notifications/{notification_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == notification_id


def test_get_notification_not_found(client):
    resp = client.get("/notifications/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_create_notification_requires_body_or_template(client):
    resp = client.post("/notifications", json={"user_id": "user-1"})
    assert resp.status_code == 422


def test_create_notification_unknown_template_returns_400(client):
    resp = client.post(
        "/notifications",
        json={"user_id": "user-1", "template_name": "nope", "variables": {}},
    )
    assert resp.status_code == 400


def test_idempotency_key_prevents_duplicate_notification(client):
    payload = {
        "user_id": "user-1",
        "channels": ["email"],
        "body": "Hi",
        "variables": {},
        "idempotency_key": "key-123",
    }
    first = client.post("/notifications", json=payload)
    second = client.post("/notifications", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_opted_out_channel_is_excluded_from_notification(client):
    client.post(
        "/users/user-2/preferences",
        json={"preferences": [{"channel": "sms", "enabled": False}]},
    )

    resp = client.post(
        "/notifications",
        json={
            "user_id": "user-2",
            "channels": ["email", "sms"],
            "body": "Hi",
            "variables": {},
        },
    )
    channels_sent = {c["channel"] for c in resp.json()["channels"]}
    assert channels_sent == {"email"}


def test_all_channels_opted_out_returns_422(client):
    client.post(
        "/users/user-3/preferences",
        json={"preferences": [{"channel": "email", "enabled": False}]},
    )
    resp = client.post(
        "/notifications",
        json={"user_id": "user-3", "channels": ["email"], "body": "Hi", "variables": {}},
    )
    assert resp.status_code == 422


def test_missing_template_variable_marks_channel_failed(client):
    resp = client.post(
        "/notifications",
        json={
            "user_id": "user-1",
            "channels": ["email"],
            "body": "Hi {{missing}}",
            "variables": {},
        },
    )
    body = resp.json()
    assert body["status"] == "failed"
    assert body["channels"][0]["status"] == "failed"
    assert "missing" in body["channels"][0]["last_error"]


def test_rate_limit_blocks_after_threshold(client, monkeypatch):
    import app.api.routes.notifications as notif_routes

    # Tighten the limit just for this test so we don't need 100 requests.
    # monkeypatch.setattr auto-reverts this after the test, unlike a plain
    # attribute assignment which would leak into every later test sharing
    # the same module-level rate_limiter singleton.
    monkeypatch.setattr(notif_routes.rate_limiter, "max_requests", 2)

    payload = {"user_id": "rate-limited-user", "channels": ["email"], "body": "Hi", "variables": {}}
    r1 = client.post("/notifications", json=payload)
    r2 = client.post("/notifications", json=payload)
    r3 = client.post("/notifications", json=payload)

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r3.status_code == 429
    assert "Retry-After" in r3.headers
