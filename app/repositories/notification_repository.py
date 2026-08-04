from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.base import AttemptStatus, Channel, ChannelDeliveryStatus, NotificationStatus
from app.models.notification import DeliveryAttempt, Notification, NotificationChannel


class NotificationRepository:
    def __init__(self, db: Session):
        self.db = db

    # --- Notification ---

    def get_by_idempotency_key(self, key: str) -> Notification | None:
        stmt = (
            select(Notification)
            .options(selectinload(Notification.channels))
            .where(Notification.idempotency_key == key)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def get_by_id(self, notification_id: str) -> Notification | None:
        stmt = (
            select(Notification)
            .options(selectinload(Notification.channels))
            .where(Notification.id == notification_id)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def create(self, notification: Notification) -> Notification:
        self.db.add(notification)
        self.db.flush()
        return notification

    def list_for_user(self, user_id: str, page: int = 1, page_size: int = 20) -> tuple[list[Notification], int]:
        base_stmt = select(Notification).where(Notification.user_id == user_id)

        total = self.db.execute(
            select(func.count()).select_from(base_stmt.subquery())
        ).scalar_one()

        stmt = (
            base_stmt.options(selectinload(Notification.channels))
            .order_by(Notification.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = list(self.db.execute(stmt).scalars().all())
        return items, total

    def update_status(self, notification: Notification, status: NotificationStatus) -> None:
        notification.status = status
        self.db.flush()

    # --- NotificationChannel ---

    def get_channel_by_id(self, channel_row_id: str) -> NotificationChannel | None:
        stmt = select(NotificationChannel).where(NotificationChannel.id == channel_row_id)
        return self.db.execute(stmt).scalar_one_or_none()

    def create_channel(self, notification_channel: NotificationChannel) -> NotificationChannel:
        self.db.add(notification_channel)
        self.db.flush()
        return notification_channel

    def update_channel_status(
        self,
        channel_row: NotificationChannel,
        status: ChannelDeliveryStatus,
        error: str | None = None,
        next_retry_at: datetime | None = None,
    ) -> None:
        channel_row.status = status
        channel_row.last_error = error
        channel_row.next_retry_at = next_retry_at
        self.db.flush()

    def increment_attempt_count(self, channel_row: NotificationChannel) -> None:
        channel_row.attempt_count += 1
        self.db.flush()

    # --- DeliveryAttempt (audit log) ---

    def record_attempt(
        self,
        notification_channel_id: str,
        attempt_number: int,
        status: AttemptStatus,
        error_message: str | None = None,
        provider_response: dict | None = None,
    ) -> DeliveryAttempt:
        attempt = DeliveryAttempt(
            notification_channel_id=notification_channel_id,
            attempt_number=attempt_number,
            status=status,
            error_message=error_message,
            provider_response=provider_response,
        )
        self.db.add(attempt)
        self.db.flush()
        return attempt

    # --- Analytics (bonus) ---

    def channel_stats(self, since: datetime | None = None) -> list[dict]:
        stmt = select(
            NotificationChannel.channel,
            NotificationChannel.status,
            func.count().label("count"),
        ).group_by(NotificationChannel.channel, NotificationChannel.status)
        if since:
            stmt = stmt.where(NotificationChannel.created_at >= since)
        rows = self.db.execute(stmt).all()
        return [{"channel": r.channel, "status": r.status, "count": r.count} for r in rows]
