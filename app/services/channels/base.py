import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.services.circuit_breaker import CircuitBreaker

logger = logging.getLogger("notification_service.channels")


@dataclass
class SendResult:
    success: bool
    provider_response: dict
    error_message: str | None = None


class ChannelSender(ABC):
    """
    Interface every channel sender implements. Real implementations would call
    out to SendGrid/Twilio/FCM etc; per the assignment scope we mock the
    actual provider call and focus on the service architecture around it.
    """

    @abstractmethod
    def send(self, *, recipient: str, subject: str | None, body: str) -> SendResult:
        raise NotImplementedError


class CircuitBreakerSender(ChannelSender):
    """
    Wraps another ChannelSender with a per-channel circuit breaker. When the
    breaker is open, `send()` fails fast with a SendResult (not an exception)
    so it slots into the worker's existing retry/backoff path unchanged --
    a circuit-open rejection is just treated as one more failed attempt,
    without the cost of actually calling the (already struggling) provider.
    """

    def __init__(self, inner: ChannelSender, breaker: CircuitBreaker, channel_name: str):
        self._inner = inner
        self._breaker = breaker
        self._channel_name = channel_name

    def send(self, *, recipient: str, subject: str | None, body: str) -> SendResult:
        if not self._breaker.allow_request(self._channel_name):
            error = f"Circuit breaker open for {self._channel_name} (too many recent failures)"
            logger.warning("circuit_open_request_rejected", extra={"channel": self._channel_name})
            return SendResult(success=False, provider_response={}, error_message=error)

        result = self._inner.send(recipient=recipient, subject=subject, body=body)
        if result.success:
            self._breaker.record_success(self._channel_name)
        else:
            self._breaker.record_failure(self._channel_name)
        return result
