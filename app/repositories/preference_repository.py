from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.base import Channel
from app.models.user_preference import UserPreference


class PreferenceRepository:
    """
    Data-access layer for user channel preferences.

    Design rule: absence of a row means "opted in" (default-on), because
    requiring an explicit opt-in row for every channel before a brand-new
    user can receive anything would silently drop notifications for users
    who haven't visited a settings page yet. Opt-OUT is always explicit.
    """

    def __init__(self, db: Session):
        self.db = db

    def get_all_for_user(self, user_id: str) -> list[UserPreference]:
        stmt = select(UserPreference).where(UserPreference.user_id == user_id)
        return list(self.db.execute(stmt).scalars().all())

    def get_enabled_channels(self, user_id: str, requested: list[Channel] | None) -> list[Channel]:
        """
        Given an optional list of requested channels, return the subset the
        user has NOT explicitly opted out of. If `requested` is None, defaults
        to all channels minus explicit opt-outs.
        """
        rows = {p.channel: p.enabled for p in self.get_all_for_user(user_id)}
        candidate_channels = requested if requested is not None else list(Channel)

        enabled = []
        for ch in candidate_channels:
            # default True (opted in) unless there's an explicit disabled row
            if rows.get(ch, True):
                enabled.append(ch)
        return enabled

    def upsert(self, user_id: str, channel: Channel, enabled: bool) -> UserPreference:
        stmt = select(UserPreference).where(
            UserPreference.user_id == user_id, UserPreference.channel == channel
        )
        existing = self.db.execute(stmt).scalar_one_or_none()
        if existing:
            existing.enabled = enabled
            self.db.flush()
            return existing

        pref = UserPreference(user_id=user_id, channel=channel, enabled=enabled)
        self.db.add(pref)
        self.db.flush()
        return pref

    def bulk_upsert(self, user_id: str, updates: list[tuple[Channel, bool]]) -> list[UserPreference]:
        return [self.upsert(user_id, channel, enabled) for channel, enabled in updates]
