import logging

from sqlalchemy.orm import Session

from app.core.queue import get_queue_for_priority
from app.models.base import ChannelDeliveryStatus, NotificationStatus
from app.models.notification import Notification, NotificationChannel
from app.repositories.notification_repository import NotificationRepository
from app.repositories.preference_repository import PreferenceRepository
from app.repositories.template_repository import TemplateRepository
from app.schemas.notification import NotificationCreateRequest

logger = logging.getLogger("notification_service.service")


class TemplateNotFoundError(Exception):
    pass


class NoEligibleChannelsError(Exception):
    """Raised when every requested channel is either unspecified or opted out."""


class NotificationService:
    def __init__(self, db: Session):
        self.db = db
        self.notifications = NotificationRepository(db)
        self.preferences = PreferenceRepository(db)
        self.templates = TemplateRepository(db)

    def create_notification(self, req: NotificationCreateRequest) -> Notification:
        # --- Idempotency: return the existing notification untouched if this
        # key was already processed, instead of creating a duplicate. ---
        if req.idempotency_key:
            existing = self.notifications.get_by_idempotency_key(req.idempotency_key)
            if existing:
                logger.info(
                    "idempotent_replay",
                    extra={"idempotency_key": req.idempotency_key, "notification_id": existing.id},
                )
                return existing

        # --- Resolve template (if any) up front, so a bad template name
        # fails the request immediately rather than failing async later. ---
        subject, body = req.subject, req.body
        template_id = None
        if req.template_name:
            template = self.templates.get_by_name(req.template_name)
            if not template:
                raise TemplateNotFoundError(f"Template '{req.template_name}' not found")
            template_id = template.id
            subject, body = template.subject, template.body

        # --- Resolve which channels actually get sent, respecting opt-outs ---
        enabled_channels = self.preferences.get_enabled_channels(req.user_id, req.channels)
        if not enabled_channels:
            raise NoEligibleChannelsError(
                "No eligible channels: user has opted out of all requested channels"
            )

        notification = Notification(
            user_id=req.user_id,
            priority=req.priority,
            status=NotificationStatus.PENDING,
            template_id=template_id,
            raw_subject=subject,
            raw_body=body,
            variables=req.variables,
            idempotency_key=req.idempotency_key,
        )
        self.notifications.create(notification)

        channel_rows: list[NotificationChannel] = []
        for channel in enabled_channels:
            row = NotificationChannel(
                notification_id=notification.id,
                channel=channel,
                status=ChannelDeliveryStatus.PENDING,
            )
            self.notifications.create_channel(row)
            channel_rows.append(row)

        # Commit before enqueueing -- the worker will load this row by ID from
        # its own DB session, so it must already be durably persisted.
        self.db.commit()

        queue = get_queue_for_priority(req.priority)
        for row in channel_rows:
            queue.enqueue(
                "app.workers.notification_worker.process_notification_channel",
                row.id,
                job_timeout=30,
            )

        logger.info(
            "notification_created",
            extra={
                "notification_id": notification.id,
                "user_id": req.user_id,
                "priority": req.priority.value,
                "channels": [c.value for c in enabled_channels],
                "idempotency_key": req.idempotency_key,
            },
        )
        return notification
