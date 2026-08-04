def test_batch_create_happy_path(client):
    resp = client.post(
        "/notifications/batch",
        json={
            "user_ids": ["batch-user-1", "batch-user-2", "batch-user-3"],
            "channels": ["email"],
            "body": "Hello {{name}}",
            "variables": {"name": "everyone"},
        },
    )
    assert resp.status_code == 207
    body = resp.json()
    assert body["total_requested"] == 3
    assert body["created_count"] == 3
    assert {r["status"] for r in body["results"]} == {"created"}
    assert {r["user_id"] for r in body["results"]} == {"batch-user-1", "batch-user-2", "batch-user-3"}
    assert all(r["notification_id"] for r in body["results"])

    # Each recipient actually got their own notification, independently delivered.
    for user_id in ["batch-user-1", "batch-user-2", "batch-user-3"]:
        history = client.get(f"/users/{user_id}/notifications")
        assert history.json()["total"] == 1
        assert history.json()["items"][0]["status"] == "delivered"


def test_batch_requires_template_or_body(client):
    resp = client.post("/notifications/batch", json={"user_ids": ["u1"]})
    assert resp.status_code == 422


def test_batch_requires_at_least_one_user(client):
    resp = client.post("/notifications/batch", json={"user_ids": [], "body": "hi"})
    assert resp.status_code == 422


def test_batch_unknown_template_returns_400_before_creating_anything(client):
    resp = client.post(
        "/notifications/batch",
        json={"user_ids": ["batch-user-a", "batch-user-b"], "template_name": "does-not-exist"},
    )
    assert resp.status_code == 400

    # Fail-fast: no partial batch should have been created for either user.
    for user_id in ["batch-user-a", "batch-user-b"]:
        history = client.get(f"/users/{user_id}/notifications")
        assert history.json()["total"] == 0


def test_batch_skips_users_who_opted_out_without_failing_others(client):
    client.post(
        "/users/batch-optout-user/preferences",
        json={"preferences": [{"channel": "email", "enabled": False}]},
    )

    resp = client.post(
        "/notifications/batch",
        json={
            "user_ids": ["batch-optout-user", "batch-normal-user"],
            "channels": ["email"],
            "body": "hi",
        },
    )
    assert resp.status_code == 207
    body = resp.json()
    results = {r["user_id"]: r for r in body["results"]}
    assert results["batch-optout-user"]["status"] == "skipped_no_eligible_channels"
    assert results["batch-normal-user"]["status"] == "created"
    assert body["created_count"] == 1


def test_batch_rate_limits_individual_users_without_failing_others(client, monkeypatch):
    import app.api.routes.notifications as notif_routes

    monkeypatch.setattr(notif_routes.rate_limiter, "max_requests", 1)

    resp = client.post(
        "/notifications/batch",
        json={
            "user_ids": ["rl-batch-user", "rl-batch-user", "other-batch-user"],
            "channels": ["email"],
            "body": "hi",
        },
    )
    assert resp.status_code == 207
    body = resp.json()
    results = [r for r in body["results"] if r["user_id"] == "rl-batch-user"]
    assert results[0]["status"] == "created"
    assert results[1]["status"] == "rate_limited"
    other = [r for r in body["results"] if r["user_id"] == "other-batch-user"][0]
    assert other["status"] == "created"


def test_batch_idempotency_key_prefix_dedupes_per_user(client):
    payload = {
        "user_ids": ["idem-batch-1", "idem-batch-2"],
        "channels": ["email"],
        "body": "hi",
        "idempotency_key_prefix": "campaign-42",
    }
    first = client.post("/notifications/batch", json=payload)
    second = client.post("/notifications/batch", json=payload)

    assert first.status_code == 207
    assert second.status_code == 207

    first_ids = {r["user_id"]: r["notification_id"] for r in first.json()["results"]}
    second_ids = {r["user_id"]: r["notification_id"] for r in second.json()["results"]}
    assert first_ids == second_ids  # same notification returned, not a duplicate

    history = client.get("/users/idem-batch-1/notifications")
    assert history.json()["total"] == 1
