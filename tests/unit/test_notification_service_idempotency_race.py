from sqlalchemy import select

from app.models.notification import Notification
from app.repositories.notification_repository import NotificationRepository
from app.schemas.notification import NotificationCreateRequest
from app.services.notification_service import NotificationService


class _NoOpQueue:
    def enqueue(self, *args, **kwargs):
        pass


def test_create_notification_recovers_from_idempotency_race(db_session, monkeypatch):
    """
    Reproduces the exact race window between two concurrent requests sharing
    an idempotency key: both pass the "does it exist yet" check before either
    has committed, so both proceed to insert. The DB's unique constraint on
    idempotency_key catches the second commit -- create_notification must
    recover by returning the winner's row instead of propagating a 500.
    """
    monkeypatch.setattr(
        "app.services.notification_service.get_queue_for_priority",
        lambda priority: _NoOpQueue(),
    )

    # The first two lookups are each request's own pre-insert existence
    # check -- patched to "not found" to simulate both racing past it before
    # either has committed. Every call after that is the real lookup, which
    # includes the recovery-path re-query inside _create_and_enqueue's
    # IntegrityError handler -- that one must see the real, committed row.
    original_get = NotificationRepository.get_by_idempotency_key
    call_count = {"n": 0}

    def flaky_get(self, key):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            return None
        return original_get(self, key)

    monkeypatch.setattr(NotificationRepository, "get_by_idempotency_key", flaky_get)

    service = NotificationService(db_session)
    req = NotificationCreateRequest(user_id="user-race", body="hello", idempotency_key="race-key")

    first = service.create_notification(req)
    second = service.create_notification(req)

    assert second.id == first.id

    rows = db_session.execute(
        select(Notification).where(Notification.idempotency_key == "race-key")
    ).scalars().all()
    assert len(rows) == 1
