import logging
import sys

from pythonjsonlogger import jsonlogger

from app.config import settings


def configure_logging() -> None:
    """
    Structured JSON logging so log fields (notification_id, channel, status,
    etc, passed via `extra={...}`) are queryable in any log aggregator
    (CloudWatch, Datadog, ELK) without regex parsing of free-text messages.
    """
    handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)

    # Quiet noisy third-party loggers at INFO
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
