import logging

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import notifications, users
from app.config import settings
from app.core.logging import configure_logging

configure_logging()
logger = logging.getLogger("notification_service.main")

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Multi-channel (email/SMS/push) notification service with priority "
        "queueing, retries, idempotency, rate limiting, and delivery tracking."
    ),
)

app.include_router(notifications.router)
app.include_router(users.router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Consistent 422 error shape for input validation failures."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": jsonable_encoder(exc.errors())},
    )


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok", "service": settings.app_name}
