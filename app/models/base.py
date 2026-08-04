import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def str_enum_column(enum_cls: type[enum.Enum], length: int = 20) -> SAEnum:
    """
    Stores the Enum's `.value` (e.g. "delivered") rather than SQLAlchemy's
    default of the member `.name` (e.g. "DELIVERED"). Without this, raw SQL
    queries against the DB show different casing than the API's JSON
    responses, which is confusing for anyone inspecting the schema directly.
    """
    return SAEnum(enum_cls, native_enum=False, length=length, values_callable=lambda e: [m.value for m in e])


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def generate_uuid() -> str:
    return str(uuid.uuid4())


class Channel(str, enum.Enum):
    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"


class Priority(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class NotificationStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    PARTIALLY_FAILED = "partially_failed"  # some channels succeeded, some didn't


class ChannelDeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"  # user opted out of this channel


class AttemptStatus(str, enum.Enum):
    SUCCESS = "success"
    FAILURE = "failure"
