from app.services.channels.base import SendResult


class FlakySender:
    """Fails the first `fail_times` sends, then succeeds."""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.call_count = 0

    def send(self, *, recipient, subject, body):
        self.call_count += 1
        if self.call_count <= self.fail_times:
            return SendResult(success=False, provider_response={}, error_message=f"flaky failure #{self.call_count}")
        return SendResult(success=True, provider_response={"provider": "fake"})


class AlwaysFailingSender:
    def __init__(self):
        self.call_count = 0

    def send(self, *, recipient, subject, body):
        self.call_count += 1
        return SendResult(success=False, provider_response={}, error_message="permanent provider outage")


def test_channel_recovers_after_transient_failures(client, monkeypatch):
    """Fails twice, succeeds on the 3rd attempt -- within the max_retries=3 budget."""
    sender = FlakySender(fail_times=2)
    monkeypatch.setattr("app.workers.notification_worker.get_sender", lambda channel: sender)

    resp = client.post(
        "/notifications",
        json={"user_id": "flaky-user", "channels": ["email"], "body": "Hi", "variables": {}},
    )
    body = resp.json()

    assert body["status"] == "delivered"
    assert body["channels"][0]["status"] == "delivered"
    assert body["channels"][0]["attempt_count"] == 3  # 2 failures + 1 success
    assert sender.call_count == 3


def test_channel_permanently_fails_after_exhausting_retries(client, monkeypatch):
    sender = AlwaysFailingSender()
    monkeypatch.setattr("app.workers.notification_worker.get_sender", lambda channel: sender)

    resp = client.post(
        "/notifications",
        json={"user_id": "unlucky-user", "channels": ["email"], "body": "Hi", "variables": {}},
    )
    body = resp.json()

    assert body["status"] == "failed"
    assert body["channels"][0]["status"] == "failed"
    assert body["channels"][0]["last_error"] == "permanent provider outage"
    # Initial attempt + max 3 retries = 4 total attempts, then give up.
    assert body["channels"][0]["attempt_count"] == 4
    assert sender.call_count == 4


def test_delivery_attempts_are_recorded_for_each_try(client, monkeypatch, db_session):
    from app.repositories.notification_repository import NotificationRepository

    sender = FlakySender(fail_times=1)
    monkeypatch.setattr("app.workers.notification_worker.get_sender", lambda channel: sender)

    resp = client.post(
        "/notifications",
        json={"user_id": "audit-user", "channels": ["sms"], "body": "Hi", "variables": {}},
    )
    notification_id = resp.json()["id"]

    repo = NotificationRepository(db_session)
    notification = repo.get_by_id(notification_id)
    channel_row = notification.channels[0]

    db_session.refresh(channel_row)
    attempts = channel_row.attempts
    assert len(attempts) == 2
    assert attempts[0].status.value == "failure"
    assert attempts[1].status.value == "success"


def test_partially_failed_when_some_channels_succeed_and_others_exhaust_retries(client, monkeypatch):
    """
    Multi-channel notification where one channel always fails and another
    always succeeds -- the parent notification should land on
    'partially_failed', not 'failed' or 'delivered'.
    """
    failing_sender = AlwaysFailingSender()
    succeeding_sender = FlakySender(fail_times=0)

    def sender_router(channel):
        return failing_sender if channel.value == "sms" else succeeding_sender

    monkeypatch.setattr("app.workers.notification_worker.get_sender", sender_router)

    resp = client.post(
        "/notifications",
        json={"user_id": "mixed-user", "channels": ["email", "sms"], "body": "Hi", "variables": {}},
    )
    body = resp.json()
    assert body["status"] == "partially_failed"

    statuses = {c["channel"]: c["status"] for c in body["channels"]}
    assert statuses["email"] == "delivered"
    assert statuses["sms"] == "failed"
