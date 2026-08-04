import logging
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.core.db import SessionLocal
from app.core.queue import get_queue_for_priority
from app.models.base import AttemptStatus, ChannelDeliveryStatus, NotificationStatus
from app.repositories.notification_repository import NotificationRepository
from app.services.channels.factory import get_sender
from app.services.retry_policy import compute_backoff_seconds, should_retry
from app.services.template_service import render_template

logger = logging.getLogger("notification_service.worker")


def _is_channel_terminal(channel_row) -> bool:
    """
    A channel is only "done" (no further automatic action pending) if it
    DELIVERED, or if it FAILED with no retry scheduled (next_retry_at is
    None). A FAILED channel that still has next_retry_at set is mid-retry,
    not terminal -- treating it as terminal here would let the notification
    settle into "failed" for the several seconds/minutes before its retry
    actually runs and (often) succeeds.
    """
    if channel_row.status == ChannelDeliveryStatus.DELIVERED:
        return True
    if channel_row.status == ChannelDeliveryStatus.FAILED and channel_row.next_retry_at is None:
        return True
    return False


def _recompute_notification_status(repo: NotificationRepository, notification) -> None:
    """
    Aggregates per-channel statuses into the parent notification's overall
    status. Only settles into a terminal state once every channel has
    reached one of its own terminal states (DELIVERED or permanently FAILED).
    """
    channels = notification.channels
    if not all(_is_channel_terminal(c) for c in channels):
        repo.update_status(notification, NotificationStatus.PROCESSING)
        return

    delivered = [c for c in channels if c.status == ChannelDeliveryStatus.DELIVERED]
    failed = [c for c in channels if c.status == ChannelDeliveryStatus.FAILED]

    if failed and delivered:
        repo.update_status(notification, NotificationStatus.PARTIALLY_FAILED)
    elif failed:
        repo.update_status(notification, NotificationStatus.FAILED)
    else:
        repo.update_status(notification, NotificationStatus.DELIVERED)


def process_notification_channel(notification_channel_id: str) -> None:
    """
    RQ job entrypoint: sends one notification through one channel, tracks the
    attempt, and schedules a backoff retry on failure (up to settings.max_retries).

    Runs in the RQ worker process, so it opens its own DB session rather than
    reusing one from the request that enqueued it.
    """
    db = SessionLocal()
    try:
        repo = NotificationRepository(db)
        channel_row = repo.get_channel_by_id(notification_channel_id)
        if channel_row is None:
            logger.error("channel_row_not_found", extra={"notification_channel_id": notification_channel_id})
            return

        notification = channel_row.notification

        # Render content just-in-time so a template edited after creation
        # doesn't retroactively change an in-flight retry's content.
        try:
            subject = (
                render_template(notification.raw_subject, notification.variables)
                if notification.raw_subject
                else None
            )
            body = render_template(notification.raw_body, notification.variables)
        except Exception as exc:  # includes MissingTemplateVariableError
            repo.update_channel_status(channel_row, ChannelDeliveryStatus.FAILED, error=str(exc))
            _recompute_notification_status(repo, notification)
            db.commit()
            logger.error(
                "template_render_failed",
                extra={"notification_channel_id": notification_channel_id, "error": str(exc)},
            )
            return

        repo.increment_attempt_count(channel_row)
        attempt_number = channel_row.attempt_count

        sender = get_sender(channel_row.channel)
        result = sender.send(recipient=notification.user_id, subject=subject, body=body)

        if result.success:
            repo.record_attempt(
                notification_channel_id=channel_row.id,
                attempt_number=attempt_number,
                status=AttemptStatus.SUCCESS,
                provider_response=result.provider_response,
            )
            # Mock providers confirm synchronously. A real integration would
            # stay at SENT until a provider webhook confirms DELIVERED.
            repo.update_channel_status(channel_row, ChannelDeliveryStatus.DELIVERED)
            logger.info(
                "channel_delivered",
                extra={
                    "notification_id": notification.id,
                    "channel": channel_row.channel.value,
                    "attempt_number": attempt_number,
                },
            )
        else:
            repo.record_attempt(
                notification_channel_id=channel_row.id,
                attempt_number=attempt_number,
                status=AttemptStatus.FAILURE,
                error_message=result.error_message,
            )
            retries_used = attempt_number - 1
            if should_retry(retries_used, channel_row.max_retries):
                delay_seconds = compute_backoff_seconds(retries_used, settings.retry_base_delay_seconds)
                next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
                repo.update_channel_status(
                    channel_row,
                    ChannelDeliveryStatus.FAILED,
                    error=result.error_message,
                    next_retry_at=next_retry_at,
                )
                queue = get_queue_for_priority(notification.priority)
                queue.enqueue_in(
                    timedelta(seconds=delay_seconds),
                    "app.workers.notification_worker.process_notification_channel",
                    channel_row.id,
                    job_timeout=30,
                )
                logger.warning(
                    "channel_send_failed_retry_scheduled",
                    extra={
                        "notification_id": notification.id,
                        "channel": channel_row.channel.value,
                        "attempt_number": attempt_number,
                        "retry_in_seconds": delay_seconds,
                        "error": result.error_message,
                    },
                )
            else:
                repo.update_channel_status(
                    channel_row, ChannelDeliveryStatus.FAILED, error=result.error_message, next_retry_at=None
                )
                logger.error(
                    "channel_send_permanently_failed",
                    extra={
                        "notification_id": notification.id,
                        "channel": channel_row.channel.value,
                        "attempt_number": attempt_number,
                        "error": result.error_message,
                    },
                )

        _recompute_notification_status(repo, notification)
        db.commit()
    finally:
        db.close()
