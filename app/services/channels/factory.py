import os

from app.config import settings
from app.core.queue import redis_conn
from app.models.base import Channel
from app.services.channels.base import ChannelSender, CircuitBreakerSender
from app.services.channels.mock_senders import EmailSender, PushSender, SmsSender
from app.services.circuit_breaker import CircuitBreaker

# Optional: set FAILURE_SIMULATION_RATE=0.3 in the environment to see the
# retry/backoff path trigger during a manual demo. Defaults to 0 so
# automated tests stay deterministic.
_FAILURE_RATE = float(os.getenv("FAILURE_SIMULATION_RATE", "0.0"))

# Module-level singleton (mirrors the rate_limiter pattern in
# api/routes/notifications.py) so tests can swap `.redis` for fakeredis
# instead of needing a live Redis instance.
circuit_breaker = CircuitBreaker(
    redis_conn,
    failure_threshold=settings.circuit_breaker_failure_threshold,
    recovery_seconds=settings.circuit_breaker_recovery_seconds,
)

_SENDERS: dict[Channel, ChannelSender] = {
    Channel.EMAIL: CircuitBreakerSender(EmailSender(failure_rate=_FAILURE_RATE), circuit_breaker, "email"),
    Channel.SMS: CircuitBreakerSender(SmsSender(failure_rate=_FAILURE_RATE), circuit_breaker, "sms"),
    Channel.PUSH: CircuitBreakerSender(PushSender(failure_rate=_FAILURE_RATE), circuit_breaker, "push"),
}


def get_sender(channel: Channel) -> ChannelSender:
    return _SENDERS[channel]
