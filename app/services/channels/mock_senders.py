import logging
import random

from app.services.channels.base import ChannelSender, SendResult

logger = logging.getLogger("notification_service.channels")


class MockChannelSender(ChannelSender):
    """
    Simulates a third-party provider call. Per assignment scope, real
    integrations (SendGrid/Twilio/FCM) are out of scope -- this focuses on
    service design around the provider boundary instead.

    `failure_rate` lets us simulate real-world flakiness for demoing the
    retry/backoff path. It defaults to 0 so tests are deterministic; set it
    via the constructor (or FAILURE_SIMULATION_RATE env var wiring in
    channel_factory.py) to see retries in action manually.
    """

    channel_name = "generic"

    def __init__(self, failure_rate: float = 0.0):
        self.failure_rate = failure_rate

    def send(self, *, recipient: str, subject: str | None, body: str) -> SendResult:
        if random.random() < self.failure_rate:
            error = f"Simulated {self.channel_name} provider timeout"
            logger.warning(
                "channel_send_failed",
                extra={"channel": self.channel_name, "recipient": recipient, "error": error},
            )
            return SendResult(success=False, provider_response={}, error_message=error)

        logger.info(
            "channel_send_succeeded",
            extra={"channel": self.channel_name, "recipient": recipient, "subject": subject},
        )
        return SendResult(
            success=True,
            provider_response={"provider": f"mock-{self.channel_name}", "message_id": "mock-id"},
        )


class EmailSender(MockChannelSender):
    channel_name = "email"


class SmsSender(MockChannelSender):
    channel_name = "sms"


class PushSender(MockChannelSender):
    channel_name = "push"
