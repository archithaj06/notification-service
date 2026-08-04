import os

from app.models.base import Channel
from app.services.channels.base import ChannelSender
from app.services.channels.mock_senders import EmailSender, PushSender, SmsSender

# Optional: set FAILURE_SIMULATION_RATE=0.3 in the environment to see the
# retry/backoff path trigger during a manual demo. Defaults to 0 so
# automated tests stay deterministic.
_FAILURE_RATE = float(os.getenv("FAILURE_SIMULATION_RATE", "0.0"))

_SENDERS: dict[Channel, ChannelSender] = {
    Channel.EMAIL: EmailSender(failure_rate=_FAILURE_RATE),
    Channel.SMS: SmsSender(failure_rate=_FAILURE_RATE),
    Channel.PUSH: PushSender(failure_rate=_FAILURE_RATE),
}


def get_sender(channel: Channel) -> ChannelSender:
    return _SENDERS[channel]
