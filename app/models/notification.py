from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    AttemptStatus,
    Base,
    Channel,
    ChannelDeliveryStatus,
    NotificationStatus,
    Priority,
    TimestampMixin,
    generate_uuid,
    str_enum_column,
)


class Notification(Base, TimestampMixin):
    """
    A single logical notification request from a client. One Notification can
    fan out to multiple channels (email + push, say) -- each channel's delivery
    is tracked separately in NotificationChannel so a failure on SMS doesn't
    hide a success on email.

    idempotency_key is globally unique (when provided) so retried client
    requests never create a duplicate send.
    """

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    priority: Mapped[Priority] = mapped_column(
        str_enum_column(Priority), nullable=False, default=Priority.NORMAL, index=True
    )
    status: Mapped[NotificationStatus] = mapped_column(
        str_enum_column(NotificationStatus),
        nullable=False,
        default=NotificationStatus.PENDING,
        index=True,
    )

    template_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("templates.id"), nullable=True
    )
    # Raw content used when no template_id is given.
    raw_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_body: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Variables substituted into the template (or raw_body), e.g. {"name": "Priya"}
    variables: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    idempotency_key: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True, index=True
    )

    channels: Mapped[list["NotificationChannel"]] = relationship(
        back_populates="notification", cascade="all, delete-orphan"
    )


class NotificationChannel(Base, TimestampMixin):
    """
    Per-channel delivery record for a Notification. This is the unit of work
    that actually gets queued and retried -- e.g. a notification sent to
    email+sms produces two rows here, each with independent status/retry state.
    """

    __tablename__ = "notification_channels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    notification_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("notifications.id"), nullable=False, index=True
    )
    channel: Mapped[Channel] = mapped_column(str_enum_column(Channel), nullable=False)
    status: Mapped[ChannelDeliveryStatus] = mapped_column(
        str_enum_column(ChannelDeliveryStatus),
        nullable=False,
        default=ChannelDeliveryStatus.PENDING,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    notification: Mapped["Notification"] = relationship(back_populates="channels")
    attempts: Mapped[list["DeliveryAttempt"]] = relationship(
        back_populates="notification_channel", cascade="all, delete-orphan"
    )


class DeliveryAttempt(Base, TimestampMixin):
    """
    Immutable audit log: one row per actual attempt to send through a channel.
    Never updated after creation -- gives full observability into what happened
    and when, independent of the current (mutable) status on NotificationChannel.
    """

    __tablename__ = "delivery_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    notification_channel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("notification_channels.id"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AttemptStatus] = mapped_column(
        str_enum_column(AttemptStatus), nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    notification_channel: Mapped["NotificationChannel"] = relationship(back_populates="attempts")
