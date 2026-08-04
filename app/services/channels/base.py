from abc import ABC, abstractmethod
from dataclasses import dataclass


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
