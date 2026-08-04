import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.queue import get_queue_for_priority
from app.models.base import Channel, ChannelDeliveryStatus, NotificationStatus, Priority
from app.models.notification import Notification, NotificationChannel
from app.repositories.notification_repository import NotificationRepository
from app.repositories.preference_repository import PreferenceRepository
from app.repositories.template_repository import TemplateRepository
from app.schemas.notification import BatchNotificationCreateRequest, NotificationCreateRequest
from app.services.rate_limiter import RateLimitExceededError, RateLimiter

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

    def _resolve_content(
        self, template_name: str | None, subject: str | None, body: str | None
    ) -> tuple[str | None, str | None, str | None]:
        """Returns (template_id, subject, body). Raises TemplateNotFoundError."""
        if not template_name:
            return None, subject, body

        template = self.templates.get_by_name(template_name)
        if not template:
            raise TemplateNotFoundError(f"Template '{template_name}' not found")
        return template.id, template.subject, template.body

    def _create_and_enqueue(
        self,
        *,
        user_id: str,
        channels: list[Channel],
        priority: Priority,
        template_id: str | None,
        subject: str | None,
        body: str | None,
        variables: dict,
        idempotency_key: str | None,
    ) -> Notification:
        notification = Notification(
            user_id=user_id,
            priority=priority,
            status=NotificationStatus.PENDING,
            template_id=template_id,
            raw_subject=subject,
            raw_body=body,
            variables=variables,
            idempotency_key=idempotency_key,
        )

        try:
            # create() flushes immediately, so a unique-constraint collision on
            # idempotency_key can surface here already (not only at the final
            # commit below) -- both are covered by the same except block.
            self.notifications.create(notification)

            channel_rows: list[NotificationChannel] = []
            for channel in channels:
                row = NotificationChannel(
                    notification_id=notification.id,
                    channel=channel,
                    status=ChannelDeliveryStatus.PENDING,
                )
                self.notifications.create_channel(row)
                channel_rows.append(row)

            # Commit before enqueueing -- the worker will load these rows by ID
            # from its own DB session, so they must already be durably persisted.
            self.db.commit()
        except IntegrityError:
            # Two requests racing on the same idempotency_key can both pass
            # the "does it exist yet" check before either has committed (a
            # TOCTOU window). The unique constraint on idempotency_key catches
            # the loser here -- recover by returning the winner's row instead
            # of surfacing a 500 for what is, semantically, a duplicate send.
            self.db.rollback()
            if idempotency_key:
                existing = self.notifications.get_by_idempotency_key(idempotency_key)
                if existing:
                    logger.info(
                        "idempotent_replay_race",
                        extra={"idempotency_key": idempotency_key, "notification_id": existing.id},
                    )
                    return existing
            raise

        queue = get_queue_for_priority(priority)
        for row in channel_rows:
            queue.enqueue(
                "app.workers.notification_worker.process_notification_channel",
                row.id,
                job_timeout=30,
            )

        return notification

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
        template_id, subject, body = self._resolve_content(req.template_name, req.subject, req.body)

        # --- Resolve which channels actually get sent, respecting opt-outs ---
        enabled_channels = self.preferences.get_enabled_channels(req.user_id, req.channels)
        if not enabled_channels:
            raise NoEligibleChannelsError(
                "No eligible channels: user has opted out of all requested channels"
            )

        notification = self._create_and_enqueue(
            user_id=req.user_id,
            channels=enabled_channels,
            priority=req.priority,
            template_id=template_id,
            subject=subject,
            body=body,
            variables=req.variables,
            idempotency_key=req.idempotency_key,
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

    def create_batch_notifications(
        self, req: BatchNotificationCreateRequest, rate_limiter: RateLimiter
    ) -> list[dict]:
        """
        Sends the same message to many users at once. Reuses the exact same
        per-user pipeline as create_notification (idempotency, preferences,
        rate limiting, priority queueing) so batch sends get identical
        guarantees to a single send -- one user's failure/skip/rate-limit
        never blocks or rolls back the rest of the batch.

        The template/body is resolved once up front (a bad template_name
        fails the whole batch immediately with a 400, same fail-fast
        philosophy as the single-send path) rather than once per user.
        """
        template_id, subject, body = self._resolve_content(req.template_name, req.subject, req.body)

        results: list[dict] = []
        for user_id in req.user_ids:
            try:
                rate_limiter.check_and_record(user_id)
            except RateLimitExceededError:
                results.append({"user_id": user_id, "status": "rate_limited", "notification_id": None})
                continue

            idempotency_key = f"{req.idempotency_key_prefix}:{user_id}" if req.idempotency_key_prefix else None
            if idempotency_key:
                existing = self.notifications.get_by_idempotency_key(idempotency_key)
                if existing:
                    results.append({"user_id": user_id, "status": "created", "notification_id": existing.id})
                    continue

            enabled_channels = self.preferences.get_enabled_channels(user_id, req.channels)
            if not enabled_channels:
                results.append(
                    {"user_id": user_id, "status": "skipped_no_eligible_channels", "notification_id": None}
                )
                continue

            notification = self._create_and_enqueue(
                user_id=user_id,
                channels=enabled_channels,
                priority=req.priority,
                template_id=template_id,
                subject=subject,
                body=body,
                variables=req.variables,
                idempotency_key=idempotency_key,
            )
            results.append({"user_id": user_id, "status": "created", "notification_id": notification.id})

        logger.info(
            "batch_notifications_created",
            extra={
                "total_requested": len(req.user_ids),
                "created_count": sum(1 for r in results if r["status"] == "created"),
            },
        )
        return results
