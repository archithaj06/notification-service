from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.base import Channel, ChannelDeliveryStatus, NotificationStatus, Priority


class NotificationCreateRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=128)
    channels: list[Channel] | None = Field(
        default=None,
        description="Channels to send on. If omitted, uses all channels the user is opted into.",
    )
    priority: Priority = Priority.NORMAL

    template_name: str | None = Field(default=None, description="Name of a stored template to use")
    subject: str | None = Field(default=None, max_length=255, description="Used if no template_name given")
    body: str | None = Field(default=None, description="Used if no template_name given")
    variables: dict = Field(default_factory=dict, description="Values substituted into {{placeholders}}")

    idempotency_key: str | None = Field(
        default=None, max_length=255, description="Prevents duplicate sends on retried requests"
    )

    @model_validator(mode="after")
    def _require_template_or_body(self) -> "NotificationCreateRequest":
        if not self.template_name and not self.body:
            raise ValueError("Either 'template_name' or 'body' must be provided")
        return self


class ChannelStatusResponse(BaseModel):
    channel: Channel
    status: ChannelDeliveryStatus
    attempt_count: int
    last_error: str | None = None

    model_config = {"from_attributes": True}


class NotificationResponse(BaseModel):
    id: str
    user_id: str
    priority: Priority
    status: NotificationStatus
    idempotency_key: str | None
    created_at: datetime
    updated_at: datetime
    channels: list[ChannelStatusResponse] = []

    model_config = {"from_attributes": True}


class NotificationListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[NotificationResponse]
