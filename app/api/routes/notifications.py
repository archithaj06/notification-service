import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.core.db import get_db
from app.core.queue import redis_conn
from app.repositories.notification_repository import NotificationRepository
from app.schemas.notification import NotificationCreateRequest, NotificationResponse
from app.services.notification_service import (
    NoEligibleChannelsError,
    NotificationService,
    TemplateNotFoundError,
)
from app.services.rate_limiter import RateLimitExceededError, RateLimiter

logger = logging.getLogger("notification_service.api")
router = APIRouter(prefix="/notifications", tags=["notifications"])

rate_limiter = RateLimiter(
    redis_conn, max_requests=settings.rate_limit_max_per_hour, window_seconds=settings.rate_limit_window_seconds
)


@router.post("", response_model=NotificationResponse, status_code=status.HTTP_201_CREATED)
def create_notification(req: NotificationCreateRequest, db: Session = Depends(get_db)):
    try:
        rate_limiter.check_and_record(req.user_id)
    except RateLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc

    service = NotificationService(db)
    try:
        notification = service.create_notification(req)
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except NoEligibleChannelsError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception:
        logger.exception("notification_creation_failed", extra={"user_id": req.user_id})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create notification"
        )

    return notification


@router.get("/analytics/stats")
def channel_stats(db: Session = Depends(get_db)):
    """Bonus: basic sent/failed counts by channel and status."""
    repo = NotificationRepository(db)
    return {"stats": repo.channel_stats()}


@router.get("/{notification_id}", response_model=NotificationResponse)
def get_notification(notification_id: str, db: Session = Depends(get_db)):
    repo = NotificationRepository(db)
    notification = repo.get_by_id(notification_id)
    if notification is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return notification
