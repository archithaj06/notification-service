from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Channel, TimestampMixin, generate_uuid, str_enum_column


class UserPreference(Base, TimestampMixin):
    """
    One row per (user_id, channel). Presence + enabled=True means the user
    accepts notifications on that channel. If a user has no row for a given
    channel, we default to opted-in (see PreferenceRepository for the rule) --
    this keeps onboarding frictionless: users don't have to explicitly opt in
    to every channel before they can receive anything.
    """

    __tablename__ = "user_preferences"
    __table_args__ = (UniqueConstraint("user_id", "channel", name="uq_user_channel"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    channel: Mapped[Channel] = mapped_column(str_enum_column(Channel), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
