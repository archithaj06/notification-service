from pydantic import BaseModel

from app.models.base import Channel


class ChannelPreference(BaseModel):
    channel: Channel
    enabled: bool


class PreferenceUpdateRequest(BaseModel):
    preferences: list[ChannelPreference]


class PreferenceResponse(BaseModel):
    user_id: str
    preferences: list[ChannelPreference]
