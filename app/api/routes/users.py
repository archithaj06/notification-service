from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.user_preference import UserPreference
from app.repositories.notification_repository import NotificationRepository
from app.repositories.preference_repository import PreferenceRepository
from app.schemas.notification import NotificationListResponse
from app.schemas.preference import ChannelPreference, PreferenceResponse, PreferenceUpdateRequest

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{user_id}/notifications", response_model=NotificationListResponse)
def get_user_notifications(
    user_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    repo = NotificationRepository(db)
    items, total = repo.list_for_user(user_id, page=page, page_size=page_size)
    return NotificationListResponse(total=total, page=page, page_size=page_size, items=items)


@router.get("/{user_id}/preferences", response_model=PreferenceResponse)
def get_preferences(user_id: str, db: Session = Depends(get_db)):
    repo = PreferenceRepository(db)
    rows = repo.get_all_for_user(user_id)
    stored = {r.channel: r.enabled for r in rows}

    # Report every channel explicitly, defaulting unset ones to enabled=True
    # so clients always see the effective preference, not just overrides.
    from app.models.base import Channel

    prefs = [ChannelPreference(channel=ch, enabled=stored.get(ch, True)) for ch in Channel]
    return PreferenceResponse(user_id=user_id, preferences=prefs)


@router.post("/{user_id}/preferences", response_model=PreferenceResponse)
def set_preferences(user_id: str, req: PreferenceUpdateRequest, db: Session = Depends(get_db)):
    repo = PreferenceRepository(db)
    updates = [(p.channel, p.enabled) for p in req.preferences]
    repo.bulk_upsert(user_id, updates)
    db.commit()
    return get_preferences(user_id, db)
