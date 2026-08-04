def test_get_preferences_defaults_all_enabled(client):
    resp = client.get("/users/brand-new-user/preferences")
    assert resp.status_code == 200
    prefs = {p["channel"]: p["enabled"] for p in resp.json()["preferences"]}
    assert prefs == {"email": True, "sms": True, "push": True}


def test_set_preferences_persists_opt_out(client):
    resp = client.post(
        "/users/user-x/preferences",
        json={"preferences": [{"channel": "sms", "enabled": False}]},
    )
    assert resp.status_code == 200
    prefs = {p["channel"]: p["enabled"] for p in resp.json()["preferences"]}
    assert prefs["sms"] is False
    assert prefs["email"] is True

    # Confirm it actually persisted, not just echoed back
    get_resp = client.get("/users/user-x/preferences")
    prefs2 = {p["channel"]: p["enabled"] for p in get_resp.json()["preferences"]}
    assert prefs2["sms"] is False


def test_notification_history_for_user(client):
    for i in range(3):
        client.post(
            "/notifications",
            json={"user_id": "history-user", "channels": ["email"], "body": f"msg {i}", "variables": {}},
        )
    # Unrelated user shouldn't show up
    client.post(
        "/notifications",
        json={"user_id": "other-user", "channels": ["email"], "body": "x", "variables": {}},
    )

    resp = client.get("/users/history-user/notifications")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert all(item["user_id"] == "history-user" for item in body["items"])


def test_notification_history_pagination(client):
    for i in range(5):
        client.post(
            "/notifications",
            json={"user_id": "paging-user", "channels": ["email"], "body": f"msg {i}", "variables": {}},
        )

    page1 = client.get("/users/paging-user/notifications?page=1&page_size=2")
    assert page1.status_code == 200
    assert page1.json()["total"] == 5
    assert len(page1.json()["items"]) == 2
