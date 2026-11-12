from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class DatasetDraft(BaseModel):
    name: str = Field(min_length=3, max_length=120)


class TestConversionEvent(BaseModel):
    event_name: Literal["Contact", "Lead", "Schedule", "Purchase"]
    event_time: int = Field(gt=0)
    event_id: str = Field(min_length=8, max_length=100)
    action_source: Literal["website", "business_messaging"]
    messaging_channel: Literal["whatsapp", "messenger", "instagram"] | None = None
    event_source_url: str | None = None
    user_data: dict[str, Any]
    custom_data: dict[str, Any] | None = None

    @field_validator("user_data")
    @classmethod
    def require_identity(cls, value):
        allowed = {"em", "ph", "external_id", "fbc", "fbp", "ctwa_clid", "lead_id"}
        if not value or not set(value).issubset(allowed):
            raise ValueError("user_data must contain only supported identity fields")
        return value

    @model_validator(mode="after")
    def require_source_context(self):
        if self.action_source == "business_messaging" and self.messaging_channel is None:
            raise ValueError("messaging_channel is required for business_messaging events")
        if self.action_source == "website" and not self.event_source_url:
            raise ValueError("event_source_url is required for website events")
        return self


class ConversionTestEventDraft(BaseModel):
    test_event_code: str = Field(min_length=3, max_length=100)
    event: TestConversionEvent
