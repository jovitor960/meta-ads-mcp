from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


CampaignObjective = Literal[
    "OUTCOME_AWARENESS",
    "OUTCOME_TRAFFIC",
    "OUTCOME_ENGAGEMENT",
    "OUTCOME_LEADS",
    "OUTCOME_APP_PROMOTION",
    "OUTCOME_SALES",
]


class PausedCampaignDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=400)
    objective: CampaignObjective
    buying_type: Literal["AUCTION"] = "AUCTION"
    special_ad_categories: list[str] = Field(default_factory=list)
    daily_budget_minor: int | None = Field(default=None, gt=0)


class PausedCampaignUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: str = Field(pattern=r"^[0-9]+$")
    name: str | None = Field(default=None, min_length=1, max_length=400)
    daily_budget_minor: int | None = Field(default=None, gt=0)
    lifetime_budget_minor: int | None = Field(default=None, gt=0)
    special_ad_categories: list[str] | None = None

    @model_validator(mode="after")
    def require_change_and_budget_mode(self):
        if self.daily_budget_minor is not None and self.lifetime_budget_minor is not None:
            raise ValueError("Provide either daily_budget_minor or lifetime_budget_minor, not both.")
        if (
            self.name is None
            and self.daily_budget_minor is None
            and self.lifetime_budget_minor is None
            and self.special_ad_categories is None
        ):
            raise ValueError("At least one campaign change is required.")
        return self


class PausedAdSetDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=400)
    campaign_id: str = Field(pattern=r"^[0-9]+$")
    optimization_goal: str = Field(pattern=r"^[A-Z0-9_]+$")
    billing_event: str = Field(default="IMPRESSIONS", pattern=r"^[A-Z0-9_]+$")
    bid_strategy: str = Field(default="LOWEST_COST_WITHOUT_CAP", pattern=r"^[A-Z0-9_]+$")
    targeting: dict[str, Any] = Field(min_length=1)
    daily_budget_minor: int | None = Field(default=None, gt=0)
    lifetime_budget_minor: int | None = Field(default=None, gt=0)
    start_time: str | None = None
    end_time: str | None = None
    destination_type: str | None = Field(default=None, pattern=r"^[A-Z0-9_]+$")
    promoted_object: dict[str, Any] | None = Field(
        default=None,
        min_length=1,
        description=(
            "For WHATSAPP, provide page_id and either whatsapp_phone_number (international phone number) "
            "or whats_app_business_phone_number_id (Meta asset ID), or both. "
            "The legacy whatsapp_phone_number_id alias is translated to whats_app_business_phone_number_id. "
            "Never use a phone number as an asset ID."
        ),
    )

    @model_validator(mode="after")
    def validate_budget_and_destination(self):
        if (self.daily_budget_minor is None) == (self.lifetime_budget_minor is None):
            raise ValueError("Provide exactly one budget: daily_budget_minor or lifetime_budget_minor.")
        if self.optimization_goal == "CONVERSATIONS" and self.destination_type is None:
            raise ValueError("destination_type is required when optimization_goal is CONVERSATIONS.")
        if self.destination_type == "WHATSAPP":
            promoted = dict(self.promoted_object or {})
            for field in ("page_id", "whats_app_business_phone_number_id", "whatsapp_phone_number_id"):
                if field in promoted:
                    value = promoted[field]
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (str, int))
                        or re.fullmatch(r"[0-9]+", str(value).strip()) is None
                    ):
                        raise ValueError(f"{field} must be a numeric Meta ID, not a phone number.")
                    promoted[field] = str(value).strip()
            if "whatsapp_phone_number_id" in promoted:
                legacy_id = promoted.pop("whatsapp_phone_number_id")
                if (
                    "whats_app_business_phone_number_id" in promoted
                    and promoted["whats_app_business_phone_number_id"] != legacy_id
                ):
                    raise ValueError("Conflicting WhatsApp phone number IDs in promoted_object.")
                promoted["whats_app_business_phone_number_id"] = legacy_id
            if "page_id" not in promoted or not any(
                field in promoted for field in ("whats_app_business_phone_number_id", "whatsapp_phone_number")
            ):
                raise ValueError(
                    "WHATSAPP destination requires page_id and either "
                    "whats_app_business_phone_number_id or whatsapp_phone_number in promoted_object."
                )
            if "whatsapp_phone_number" in promoted:
                phone = promoted["whatsapp_phone_number"]
                if not isinstance(phone, str):
                    raise ValueError("whatsapp_phone_number must be an international phone number string.")
                phone = re.sub(r"[\s()-]", "", phone)
                if re.fullmatch(r"\+?[1-9][0-9]{6,14}", phone) is None:
                    raise ValueError("whatsapp_phone_number must include the country code and 7 to 15 digits.")
                promoted["whatsapp_phone_number"] = phone.removeprefix("+")
            self.promoted_object = promoted
        return self


class AdImageUploadDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_url: AnyHttpUrl
    filename: str | None = Field(default=None, min_length=1, max_length=255)


class CarouselCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_url: AnyHttpUrl
    headline: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=1_000)
    image_hash: str | None = Field(default=None, min_length=1)
    image_url: AnyHttpUrl | None = None
    call_to_action_type: str | None = Field(default=None, pattern=r"^[A-Z0-9_]+$")

    @model_validator(mode="after")
    def require_one_image(self):
        if bool(self.image_hash) == bool(self.image_url):
            raise ValueError("Each carousel card requires exactly one of image_hash or image_url.")
        return self


class LinkCreativeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_id: str = Field(pattern=r"^[0-9]+$")
    instagram_user_id: str | None = Field(
        default=None,
        pattern=r"^[0-9]+$",
        description="Instagram account ID used as object_story_spec.instagram_user_id in the Meta API.",
    )
    instagram_actor_id: str | None = Field(
        default=None,
        pattern=r"^[0-9]+$",
        json_schema_extra={"deprecated": True},
        description="Legacy alias for instagram_user_id. Translated before sending to Meta; prefer instagram_user_id.",
    )
    link_url: AnyHttpUrl
    message: str = Field(min_length=1, max_length=10_000)
    headline: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=1_000)
    image_hash: str | None = Field(default=None, min_length=1)
    image_url: AnyHttpUrl | None = None
    call_to_action_type: str = Field(default="LEARN_MORE", pattern=r"^[A-Z0-9_]+$")

    cards: list[CarouselCard] | None = Field(default=None, min_length=2, max_length=10, description="Ordered image carousel cards. Use 2 to 10 cards instead of a top-level image.")

    @model_validator(mode="after")
    def require_one_image(self):
        if self.cards is not None:
            if self.image_hash is not None or self.image_url is not None:
                raise ValueError("Carousel cards cannot be combined with a top-level image.")
        elif bool(self.image_hash) == bool(self.image_url):
            raise ValueError("Provide exactly one of image_hash or image_url.")
        return self

    @model_validator(mode="after")
    def resolve_instagram_identity(self):
        if (
            self.instagram_user_id is not None
            and self.instagram_actor_id is not None
            and self.instagram_user_id != self.instagram_actor_id
        ):
            raise ValueError("Conflicting Instagram IDs: instagram_user_id and instagram_actor_id must match.")
        if self.instagram_user_id is None:
            self.instagram_user_id = self.instagram_actor_id
        return self


class PausedAdDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=400)
    adset_id: str = Field(pattern=r"^[0-9]+$")
    creative_id: str | None = Field(default=None, pattern=r"^[0-9]+$")
    creative_spec: LinkCreativeSpec | None = None

    @model_validator(mode="after")
    def require_one_creative(self):
        if bool(self.creative_id) == bool(self.creative_spec):
            raise ValueError("Provide exactly one of creative_id or creative_spec.")
        return self


class PausedAdSetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adset_id: str = Field(pattern=r"^[0-9]+$")
    name: str | None = Field(default=None, min_length=1, max_length=400)
    optimization_goal: str | None = Field(default=None, pattern=r"^[A-Z0-9_]+$")
    bid_strategy: str | None = Field(default=None, pattern=r"^[A-Z0-9_]+$")
    targeting: dict[str, Any] | None = Field(default=None, min_length=1)
    daily_budget_minor: int | None = Field(default=None, gt=0)
    lifetime_budget_minor: int | None = Field(default=None, gt=0)
    start_time: str | None = None
    end_time: str | None = None

    @model_validator(mode="after")
    def require_change(self):
        values = self.model_dump(exclude={"adset_id"}, exclude_none=True)
        if not values:
            raise ValueError("At least one ad set change is required.")
        return self


class PausedAdUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ad_id: str = Field(pattern=r"^[0-9]+$")
    name: str | None = Field(default=None, min_length=1, max_length=400)
    creative_id: str | None = Field(default=None, pattern=r"^[0-9]+$")
    creative_spec: LinkCreativeSpec | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if self.creative_id and self.creative_spec:
            raise ValueError("Provide at most one of creative_id or creative_spec.")
        if self.name is None and self.creative_id is None and self.creative_spec is None:
            raise ValueError("At least one ad change is required.")
        return self
