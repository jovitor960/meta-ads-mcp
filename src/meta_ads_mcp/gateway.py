from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlparse

import requests

from .campaigns import (
    LinkCreativeSpec,
    AdImageUploadDraft,
    PausedAdDraft,
    PausedAdSetDraft,
    PausedAdSetUpdate,
    PausedAdUpdate,
    PausedCampaignDraft,
    PausedCampaignUpdate,
)
from .conversions import ConversionTestEventDraft, DatasetDraft


ACCOUNT_FIELDS = "id,account_id,name,account_status,currency,timezone_name,amount_spent,balance,business"
CAMPAIGN_FIELDS = "id,account_id,name,objective,status,effective_status,daily_budget,lifetime_budget,created_time,updated_time"
ADSET_FIELDS = "id,account_id,campaign_id,name,status,effective_status,daily_budget,lifetime_budget,optimization_goal,billing_event,bid_strategy,targeting,start_time,end_time,created_time,updated_time"
AD_FIELDS = "id,account_id,campaign_id,adset_id,name,status,effective_status,creative,created_time,updated_time"
CREATIVE_FIELDS = "id,account_id,name,object_story_spec,asset_feed_spec,thumbnail_url,image_hash,effective_object_story_id"
INSIGHT_FIELDS = "account_id,account_name,campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,date_start,date_stop,impressions,reach,frequency,clicks,inline_link_clicks,spend,cpc,cpm,ctr,actions,action_values,cost_per_action_type,purchase_roas"
INSIGHT_LEVELS = {"account", "campaign", "adset", "ad"}
DATE_PRESETS = {"today", "yesterday", "last_7d", "last_14d", "last_30d", "this_month", "last_month", "maximum"}


class QueryError(ValueError):
    pass


class MutationError(ValueError):
    pass


class MetaApi(Protocol):
    def list_ad_accounts(self) -> list[dict[str, Any]]: ...
    def get_ad_account(self, account_id: str) -> dict[str, Any]: ...
    def list_campaigns(self, account_id: str, max_rows: int) -> list[dict[str, Any]]: ...
    def get_insights(self, account_id: str, level: str, date_preset: str, max_rows: int) -> list[dict[str, Any]]: ...
    def list_adsets(self, account_id: str, campaign_id: str | None, max_rows: int) -> list[dict[str, Any]]: ...
    def list_ads(self, account_id: str, adset_id: str | None, max_rows: int) -> list[dict[str, Any]]: ...
    def list_ad_creatives(self, account_id: str, max_rows: int) -> list[dict[str, Any]]: ...
    def get_object(self, object_id: str, fields: str) -> dict[str, Any]: ...
    def post(self, path: str, params: dict[str, Any]) -> dict[str, Any]: ...
    def upload_image(self, account_id: str, image_url: str, filename: str | None = None) -> dict[str, Any]: ...
    def list_conversion_datasets(self, account_id: str) -> list[dict[str, Any]]: ...
    def get_conversion_dataset(self, dataset_id: str) -> dict[str, Any]: ...


class MetaAdsGateway:
    def __init__(self, api: MetaApi, business_id: str | None = None, max_budget_minor: int | None = None):
        self.api = api
        self.business_id = business_id
        self.max_budget_minor = max_budget_minor

    def list_ad_accounts(self) -> list[dict[str, Any]]:
        return self.api.list_ad_accounts()

    def get_ad_account(self, account_id: str) -> dict[str, Any]:
        return self.api.get_ad_account(_account_id(account_id))

    def get_account_billing(self, account_id: str) -> dict[str, Any]:
        account_id = _account_id(account_id)
        account = self.api.get_ad_account(account_id)
        result = {
            "account_id": account_id,
            "currency": account.get("currency"),
            "balance_raw": account.get("balance"),
            "amount_spent_raw": account.get("amount_spent"),
            "available_funds": None,
            "available_funds_status": "unavailable",
            "available_funds_reason": "Meta does not expose a verified numeric available-funds field here. Do not interpret balance, spend cap or coupons as available funds.",
            "field_errors": {},
        }
        for field in ("is_prepay_account", "funding_source_details", "spend_cap", "user_tasks"):
            result[field] = None
            try:
                value = self.api.get_object(account_id, field)
                result[field] = value.get(field)
                if field not in value:
                    result["field_errors"][field] = "not_returned"
            except (QueryError, requests.RequestException):
                result["field_errors"][field] = "query_failed: field unavailable, permission denied or temporary API failure"
        details = result.get("funding_source_details")
        display = details.get("display_string", "") if isinstance(details, dict) else ""
        result["available_funds_source"] = None
        # Strict BRL format observed in Meta. Never infer funds from cards or coupons.
        match = re.fullmatch(r"Saldo dispon\u00edvel \(R\$((?:0|[1-9][0-9]{0,2}(?:\.[0-9]{3})*|[1-9][0-9]*),[0-9]{2})\s+BRL\)", display) if isinstance(display, str) else None
        if result["is_prepay_account"] is True and result["currency"] == "BRL" and match:
            result["available_funds"] = match.group(1).replace(".", "").replace(",", ".")
            result["available_funds_status"] = "reported_by_meta"
            result["available_funds_source"] = "funding_source_details.display_string"
            result["available_funds_reason"] = "Parsed from Meta's localized display string; verify original text if Meta changes its format."
        elif result["is_prepay_account"] is False:
            result["available_funds_status"] = "not_applicable"
            result["available_funds_reason"] = "Postpaid account: no prepaid available-funds value reported."
        result["queried_at"] = datetime.now(timezone.utc).isoformat()
        return result

    def list_campaigns(self, account_id: str, max_rows: int = 200) -> list[dict[str, Any]]:
        _validate_max_rows(max_rows)
        return self.api.list_campaigns(_account_id(account_id), max_rows)

    def get_insights(self, account_id: str, level: str, date_preset: str = "last_30d", max_rows: int = 500) -> list[dict[str, Any]]:
        if level not in INSIGHT_LEVELS:
            raise QueryError(f"Unsupported insight level: {level}")
        if date_preset not in DATE_PRESETS:
            raise QueryError(f"Unsupported date preset: {date_preset}")
        _validate_max_rows(max_rows)
        return self.api.get_insights(_account_id(account_id), level, date_preset, max_rows)

    def list_adsets(self, account_id: str, campaign_id: str | None = None, max_rows: int = 200) -> list[dict[str, Any]]:
        _validate_max_rows(max_rows)
        normalized_account = _account_id(account_id)
        if campaign_id is not None:
            campaign_id = _object_id(campaign_id, "campaign_id")
            _require_same_account(self.api.get_object(campaign_id, "id,account_id"), normalized_account)
        return self.api.list_adsets(normalized_account, campaign_id, max_rows)

    def list_ads(self, account_id: str, adset_id: str | None = None, max_rows: int = 200) -> list[dict[str, Any]]:
        _validate_max_rows(max_rows)
        normalized_account = _account_id(account_id)
        if adset_id is not None:
            adset_id = _object_id(adset_id, "adset_id")
            _require_same_account(self.api.get_object(adset_id, "id,account_id"), normalized_account)
        return self.api.list_ads(normalized_account, adset_id, max_rows)

    def list_ad_creatives(self, account_id: str, max_rows: int = 200) -> list[dict[str, Any]]:
        _validate_max_rows(max_rows)
        return self.api.list_ad_creatives(_account_id(account_id), max_rows)

    def list_conversion_datasets(self, account_id: str) -> list[dict[str, Any]]:
        return self.api.list_conversion_datasets(_account_id(account_id))

    def get_conversion_dataset(self, account_id: str, dataset_id: str) -> dict[str, Any]:
        account = _account_id(account_id)
        dataset = self.api.get_conversion_dataset(_object_id(dataset_id, "dataset_id"))
        _require_dataset_account(dataset, account)
        return dataset

    def validate_conversion_dataset(self, account_id: str, business_id: str, draft: DatasetDraft) -> dict[str, Any]:
        account = _account_id(account_id)
        business = _object_id(business_id, "business_id")
        self._require_account_business(account, business)
        return {"valid": True, "account_id": account, "business_id": business, "name": draft.name}

    def create_conversion_dataset(self, account_id: str, business_id: str, draft: DatasetDraft) -> dict[str, Any]:
        account = _account_id(account_id)
        business = _object_id(business_id, "business_id")
        self._require_account_business(account, business)
        created = self.api.post(f"/{business}/adspixels", {"name": draft.name, "is_crm": True})
        dataset_id = _object_id(str(created.get("id", "")), "dataset_id")
        self.api.post(f"/{dataset_id}/shared_accounts", {"account_id": account, "business": business})
        return {"id": dataset_id, "name": draft.name, "account_id": account, "business_id": business}

    def send_test_conversion_event(self, account_id: str, dataset_id: str, draft: ConversionTestEventDraft) -> dict[str, Any]:
        dataset = self.get_conversion_dataset(account_id, dataset_id)
        event = draft.event.model_dump(exclude_none=True)
        return self.api.post(f"/{dataset['id']}/events", {"data": json.dumps([event]), "test_event_code": draft.test_event_code})

    def _require_account_business(self, account_id: str, business_id: str) -> None:
        account = self.api.get_ad_account(account_id)
        owner = str(account.get("business", {}).get("id", ""))
        if owner != business_id:
            raise MutationError("Selected business does not own the authorized ad account.")

    def upload_ad_image(self, account_id: str, draft: AdImageUploadDraft) -> dict[str, Any]:
        return self.api.upload_image(
            _account_id(account_id), str(draft.image_url), draft.filename
        )

    def create_paused_campaign(self, account_id: str, draft: PausedCampaignDraft) -> dict[str, Any]:
        _validate_budget(draft.daily_budget_minor, self.max_budget_minor)
        params = _campaign_params(draft)
        result = self.api.post(f"/{_account_id(account_id)}/campaigns", params)
        return {**result, "status": "PAUSED"}

    def validate_paused_campaign(self, account_id: str, draft: PausedCampaignDraft) -> dict[str, Any]:
        _validate_budget(draft.daily_budget_minor, self.max_budget_minor)
        return _validation("create_campaign", _account_id(account_id), _campaign_params(draft))

    def update_paused_campaign(self, account_id: str, draft: PausedCampaignUpdate) -> dict[str, Any]:
        normalized_account = _account_id(account_id)
        campaign = self.api.get_object(draft.campaign_id, "id,account_id,status,objective")
        _require_same_account(campaign, normalized_account)
        _require_paused(campaign, "campaign")
        _validate_budget(draft.daily_budget_minor, self.max_budget_minor)
        _validate_budget(draft.lifetime_budget_minor, self.max_budget_minor)
        result = self.api.post(f"/{draft.campaign_id}", _campaign_update_params(draft))
        return {**result, "status": "PAUSED"}

    def validate_paused_campaign_update(self, account_id: str, draft: PausedCampaignUpdate) -> dict[str, Any]:
        normalized_account = _account_id(account_id)
        campaign = self.api.get_object(draft.campaign_id, "id,account_id,status,objective")
        _require_same_account(campaign, normalized_account)
        _require_paused(campaign, "campaign")
        _validate_budget(draft.daily_budget_minor, self.max_budget_minor)
        _validate_budget(draft.lifetime_budget_minor, self.max_budget_minor)
        return _validation("update_campaign", normalized_account, _campaign_update_params(draft))

    def create_paused_adset(self, account_id: str, draft: PausedAdSetDraft) -> dict[str, Any]:
        normalized_account = _account_id(account_id)
        campaign = self.api.get_object(draft.campaign_id, "id,account_id,status,objective")
        _require_same_account(campaign, normalized_account)
        _require_creation_parent(campaign, "campaign")
        _validate_adset_compatibility(campaign, draft)
        _validate_budget(draft.daily_budget_minor, self.max_budget_minor)
        _validate_budget(draft.lifetime_budget_minor, self.max_budget_minor)
        params = _adset_params(draft)
        result = self.api.post(f"/{normalized_account}/adsets", params)
        return {**result, "status": "PAUSED"}

    def validate_paused_adset(self, account_id: str, draft: PausedAdSetDraft) -> dict[str, Any]:
        normalized_account = _account_id(account_id)
        campaign = self.api.get_object(draft.campaign_id, "id,account_id,status,objective")
        _require_same_account(campaign, normalized_account)
        _require_creation_parent(campaign, "campaign")
        _validate_adset_compatibility(campaign, draft)
        _validate_budget(draft.daily_budget_minor, self.max_budget_minor)
        _validate_budget(draft.lifetime_budget_minor, self.max_budget_minor)
        return _validation("create_adset", normalized_account, _adset_params(draft))

    def create_paused_ad(self, account_id: str, draft: PausedAdDraft) -> dict[str, Any]:
        normalized_account = _account_id(account_id)
        adset = self.api.get_object(draft.adset_id, "id,account_id,campaign_id,status")
        _require_same_account(adset, normalized_account)
        _require_creation_parent(adset, "ad set")
        campaign = self.api.get_object(str(adset["campaign_id"]), "id,account_id,status")
        _require_same_account(campaign, normalized_account)
        _require_creation_parent(campaign, "campaign")
        params = _ad_params(draft)
        result = self.api.post(
            f"/{normalized_account}/ads",
            params,
        )
        return {**result, "status": "PAUSED"}

    def validate_paused_ad(self, account_id: str, draft: PausedAdDraft) -> dict[str, Any]:
        normalized_account = _account_id(account_id)
        adset = self.api.get_object(draft.adset_id, "id,account_id,campaign_id,status")
        _require_same_account(adset, normalized_account)
        _require_creation_parent(adset, "ad set")
        campaign = self.api.get_object(str(adset["campaign_id"]), "id,account_id,status")
        _require_same_account(campaign, normalized_account)
        _require_creation_parent(campaign, "campaign")
        return _validation("create_ad", normalized_account, _ad_params(draft))

    def update_paused_adset(self, account_id: str, draft: PausedAdSetUpdate) -> dict[str, Any]:
        normalized_account = _account_id(account_id)
        adset = self.api.get_object(draft.adset_id, "id,account_id,status")
        _require_same_account(adset, normalized_account)
        _require_paused(adset, "ad set")
        _validate_budget(draft.daily_budget_minor, self.max_budget_minor)
        _validate_budget(draft.lifetime_budget_minor, self.max_budget_minor)
        params = _adset_update_params(draft)
        result = self.api.post(f"/{draft.adset_id}", params)
        return {**result, "status": "PAUSED"}

    def validate_paused_adset_update(self, account_id: str, draft: PausedAdSetUpdate) -> dict[str, Any]:
        normalized_account = _account_id(account_id)
        adset = self.api.get_object(draft.adset_id, "id,account_id,status")
        _require_same_account(adset, normalized_account)
        _require_paused(adset, "ad set")
        _validate_budget(draft.daily_budget_minor, self.max_budget_minor)
        _validate_budget(draft.lifetime_budget_minor, self.max_budget_minor)
        return _validation("update_adset", normalized_account, _adset_update_params(draft))

    def update_paused_ad(self, account_id: str, draft: PausedAdUpdate) -> dict[str, Any]:
        normalized_account = _account_id(account_id)
        ad = self.api.get_object(draft.ad_id, "id,account_id,status")
        _require_same_account(ad, normalized_account)
        _require_paused(ad, "ad")
        params = _ad_update_params(draft)
        result = self.api.post(f"/{draft.ad_id}", params)
        return {**result, "status": "PAUSED"}

    def validate_paused_ad_update(self, account_id: str, draft: PausedAdUpdate) -> dict[str, Any]:
        normalized_account = _account_id(account_id)
        ad = self.api.get_object(draft.ad_id, "id,account_id,status")
        _require_same_account(ad, normalized_account)
        _require_paused(ad, "ad")
        return _validation("update_ad", normalized_account, _ad_update_params(draft))


class PostgresTokenSource:
    def __init__(self, repository, cipher):
        self.repository = repository
        self.cipher = cipher

    def get(self) -> str:
        return self.cipher.decrypt(self.repository.get_encrypted_access_token())


class MetaGraphApi:
    def __init__(self, *, token_source, app_secret: str, graph_version: str = "v26.0", timeout: int = 30):
        self.token_source = token_source
        self.app_secret = app_secret
        self.base_url = f"https://graph.facebook.com/{graph_version}"
        self.timeout = timeout

    def list_ad_accounts(self) -> list[dict[str, Any]]:
        token = self.token_source.get()
        businesses = self._all("/me/businesses", token, {"fields": "id,name", "limit": 500})
        accounts: dict[str, dict[str, Any]] = {}
        for account in self._all("/me/adaccounts", token, {"fields": ACCOUNT_FIELDS, "limit": 500}):
            account["business_ids"] = []
            accounts[account["id"]] = account
        for business in businesses:
            for edge in ("owned_ad_accounts", "client_ad_accounts"):
                for account in self._all(f"/{business['id']}/{edge}", token, {"fields": ACCOUNT_FIELDS, "limit": 500}):
                    existing = accounts.setdefault(account["id"], account)
                    existing.setdefault("business_ids", [])
                    if str(business["id"]) not in existing["business_ids"]:
                        existing["business_ids"].append(str(business["id"]))
        return list(accounts.values())

    def get_ad_account(self, account_id: str) -> dict[str, Any]:
        return self._get(f"/{account_id}", {"fields": ACCOUNT_FIELDS})

    def list_campaigns(self, account_id: str, max_rows: int) -> list[dict[str, Any]]:
        return self._all(f"/{account_id}/campaigns", self.token_source.get(), {"fields": CAMPAIGN_FIELDS, "limit": min(max_rows, 500)})[:max_rows]

    def get_insights(self, account_id: str, level: str, date_preset: str, max_rows: int) -> list[dict[str, Any]]:
        return self._all(f"/{account_id}/insights", self.token_source.get(), {"fields": INSIGHT_FIELDS, "level": level, "date_preset": date_preset, "limit": min(max_rows, 500)})[:max_rows]

    def list_adsets(self, account_id: str, campaign_id: str | None, max_rows: int) -> list[dict[str, Any]]:
        owner = campaign_id or account_id
        return self._all(f"/{owner}/adsets", self.token_source.get(), {"fields": ADSET_FIELDS, "limit": min(max_rows, 500)})[:max_rows]

    def list_ads(self, account_id: str, adset_id: str | None, max_rows: int) -> list[dict[str, Any]]:
        owner = adset_id or account_id
        return self._all(f"/{owner}/ads", self.token_source.get(), {"fields": AD_FIELDS, "limit": min(max_rows, 500)})[:max_rows]

    def list_ad_creatives(self, account_id: str, max_rows: int) -> list[dict[str, Any]]:
        return self._all(f"/{account_id}/adcreatives", self.token_source.get(), {"fields": CREATIVE_FIELDS, "limit": min(max_rows, 500)})[:max_rows]

    def list_conversion_datasets(self, account_id: str) -> list[dict[str, Any]]:
        return self._all(f"/{account_id}/adspixels", self.token_source.get(), {"fields": "id,name,owner_business,last_fired_time", "limit": 500})

    def get_conversion_dataset(self, dataset_id: str) -> dict[str, Any]:
        dataset = self._get(f"/{dataset_id}", {"fields": "id,name,owner_business,last_fired_time"})
        business = str(dataset.get("owner_business", {}).get("id", ""))
        params = {"fields": "id", "limit": 500}
        if business:
            params["business"] = business
        shared = self._all(f"/{dataset_id}/shared_accounts", self.token_source.get(), params)
        dataset["shared_accounts"] = [row.get("id") for row in shared]
        return dataset

    def get_object(self, object_id: str, fields: str) -> dict[str, Any]:
        return self._get(f"/{object_id}", {"fields": fields})

    def post(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        token = self.token_source.get()
        secured = self._secured(params, token)
        response = requests.post(f"{self.base_url}{path}", data=secured, timeout=self.timeout)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise MutationError(_meta_error_message(response)) from exc
        return response.json()

    def upload_image(
        self, account_id: str, image_url: str, filename: str | None = None
    ) -> dict[str, Any]:
        token = self.token_source.get()
        try:
            image_response = requests.get(image_url, timeout=self.timeout)
            image_response.raise_for_status()
        except requests.RequestException as exc:
            raise MutationError("Unable to download the image from image_url.") from exc

        parsed_name = urlparse(image_url).path.rsplit("/", 1)[-1]
        upload_name = filename or parsed_name or "image"
        content_type = image_response.headers.get("content-type", "application/octet-stream")
        secured = self._secured({}, token)
        response = requests.post(
            f"{self.base_url}/{account_id}/adimages",
            data=secured,
            files={"filename": (upload_name, image_response.content, content_type)},
            timeout=self.timeout,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise MutationError(_meta_error_message(response)) from exc
        return response.json()

    def _get(self, path: str, params: dict[str, Any], token: str | None = None) -> dict[str, Any]:
        token = token or self.token_source.get()
        secured = self._secured(params, token)
        response = requests.get(f"{self.base_url}{path}", params=secured, timeout=self.timeout)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise QueryError(_meta_error_message(response)) from exc
        return response.json()

    def _secured(self, params: dict[str, Any], token: str) -> dict[str, Any]:
        return {**params, "access_token": token, "appsecret_proof": hmac.new(self.app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()}

    def _all(self, path: str, token: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        payload = self._get(path, params, token)
        rows = list(payload.get("data", []))
        while payload.get("paging", {}).get("next") and len(rows) < 5000:
            response = requests.get(payload["paging"]["next"], timeout=self.timeout)
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise QueryError(_meta_error_message(response)) from exc
            payload = response.json()
            rows.extend(payload.get("data", []))
        return rows[:5000]


def _account_id(value: str) -> str:
    normalized = value.strip().lower().removeprefix("act_").replace("-", "")
    if not normalized.isdigit():
        raise QueryError("Meta ad account ID must contain only digits or hyphens.")
    return f"act_{normalized}"


def _object_id(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized.isdigit():
        raise QueryError(f"{field} must contain only digits.")
    return normalized


def _validate_max_rows(max_rows: int) -> None:
    if not 1 <= max_rows <= 500:
        raise QueryError("max_rows must be between 1 and 500")


def _validate_budget(value: int | None, maximum: int | None) -> None:
    if value is not None and maximum is not None and value > maximum:
        raise MutationError("Budget exceeds the configured maximum.")


def _validate_adset_compatibility(campaign: dict[str, Any], draft: PausedAdSetDraft) -> None:
    if draft.optimization_goal != "CONVERSATIONS":
        return
    objective = str(campaign.get("objective") or "")
    allowed_objectives = {"OUTCOME_ENGAGEMENT", "OUTCOME_LEADS"}
    if objective and objective not in allowed_objectives:
        allowed = ", ".join(sorted(allowed_objectives))
        raise MutationError(
            f"optimization_goal=CONVERSATIONS is not compatible with campaign objective "
            f"{objective}; use one of: {allowed}."
        )


def _require_creation_parent(resource: dict[str, Any], kind: str) -> None:
    if resource.get("status") not in {"ACTIVE", "PAUSED"}:
        raise MutationError(f"The parent {kind} must be ACTIVE or PAUSED before creating a paused child.")


def _require_paused(resource: dict[str, Any], kind: str) -> None:
    if resource.get("status") != "PAUSED":
        raise MutationError(f"The {kind} must be PAUSED before this operation.")


def _require_same_account(resource: dict[str, Any], account_id: str) -> None:
    resource_account = _account_id(str(resource.get("account_id", "")))
    if resource_account != account_id:
        raise MutationError("The resource does not belong to the requested ad account.")


def _creative_payload(spec: LinkCreativeSpec | None) -> dict[str, Any]:
    if spec is None:
        raise MutationError("Creative specification is required.")
    link_data: dict[str, Any] = {
        "link": str(spec.link_url),
        "message": spec.message,
        "call_to_action": {"type": spec.call_to_action_type},
    }
    if spec.headline is not None:
        link_data["name"] = spec.headline
    if spec.description is not None:
        link_data["description"] = spec.description
    if spec.image_hash is not None:
        link_data["image_hash"] = spec.image_hash
    if spec.image_url is not None:
        link_data["picture"] = str(spec.image_url)
    if spec.cards is not None:
        attachments = []
        for card in spec.cards:
            attachment = {
                "link": str(card.link_url),
                "call_to_action": {"type": card.call_to_action_type or spec.call_to_action_type},
            }
            if card.headline is not None:
                attachment["name"] = card.headline
            if card.description is not None:
                attachment["description"] = card.description
            if card.image_hash is not None:
                attachment["image_hash"] = card.image_hash
            if card.image_url is not None:
                attachment["picture"] = str(card.image_url)
            attachments.append(attachment)
        link_data["child_attachments"] = attachments
        link_data["multi_share_optimized"] = False
        link_data["multi_share_end_card"] = False
    story: dict[str, Any] = {"page_id": spec.page_id, "link_data": link_data}
    if spec.instagram_user_id is not None:
        story["instagram_user_id"] = spec.instagram_user_id
    return {"object_story_spec": story}


def _campaign_params(draft: PausedCampaignDraft) -> dict[str, Any]:
    params: dict[str, Any] = {
        "name": draft.name,
        "objective": draft.objective,
        "buying_type": draft.buying_type,
        "special_ad_categories": json.dumps(draft.special_ad_categories),
        "is_adset_budget_sharing_enabled": "false",
        "status": "PAUSED",
    }
    if draft.daily_budget_minor is not None:
        params["daily_budget"] = draft.daily_budget_minor
    return params


def _campaign_update_params(draft: PausedCampaignUpdate) -> dict[str, Any]:
    params: dict[str, Any] = {"status": "PAUSED"}
    direct = {
        "name": draft.name,
        "daily_budget": draft.daily_budget_minor,
        "lifetime_budget": draft.lifetime_budget_minor,
    }
    params.update({key: value for key, value in direct.items() if value is not None})
    if draft.special_ad_categories is not None:
        params["special_ad_categories"] = json.dumps(draft.special_ad_categories)
    return params


def _adset_params(draft: PausedAdSetDraft) -> dict[str, Any]:
    params: dict[str, Any] = {
        "name": draft.name,
        "campaign_id": draft.campaign_id,
        "optimization_goal": draft.optimization_goal,
        "billing_event": draft.billing_event,
        "bid_strategy": draft.bid_strategy,
        "targeting": json.dumps(draft.targeting),
        "status": "PAUSED",
    }
    optional = {
        "daily_budget": draft.daily_budget_minor,
        "lifetime_budget": draft.lifetime_budget_minor,
        "start_time": draft.start_time,
        "end_time": draft.end_time,
        "destination_type": draft.destination_type,
    }
    params.update({key: value for key, value in optional.items() if value is not None})
    if draft.promoted_object is not None:
        params["promoted_object"] = json.dumps(draft.promoted_object)
    return params


def _ad_params(draft: PausedAdDraft) -> dict[str, Any]:
    creative = {"creative_id": draft.creative_id} if draft.creative_id else _creative_payload(draft.creative_spec)
    return {"name": draft.name, "adset_id": draft.adset_id, "creative": json.dumps(creative), "status": "PAUSED"}


def _adset_update_params(draft: PausedAdSetUpdate) -> dict[str, Any]:
    params: dict[str, Any] = {"status": "PAUSED"}
    direct = {
        "name": draft.name,
        "optimization_goal": draft.optimization_goal,
        "bid_strategy": draft.bid_strategy,
        "daily_budget": draft.daily_budget_minor,
        "lifetime_budget": draft.lifetime_budget_minor,
        "start_time": draft.start_time,
        "end_time": draft.end_time,
    }
    params.update({key: value for key, value in direct.items() if value is not None})
    if draft.targeting is not None:
        params["targeting"] = json.dumps(draft.targeting)
    return params


def _ad_update_params(draft: PausedAdUpdate) -> dict[str, Any]:
    params: dict[str, Any] = {"status": "PAUSED"}
    if draft.name is not None:
        params["name"] = draft.name
    if draft.creative_id is not None:
        params["creative"] = json.dumps({"creative_id": draft.creative_id})
    elif draft.creative_spec is not None:
        params["creative"] = json.dumps(_creative_payload(draft.creative_spec))
    return params


def _validation(operation: str, account_id: str, request: dict[str, Any]) -> dict[str, Any]:
    return {"valid": True, "operation": operation, "account_id": account_id, "forced_status": "PAUSED", "request": request}


def _meta_error_message(response: requests.Response) -> str:
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        return "Meta Graph API rejected the mutation."

    details = [str(error.get("message") or "Meta Graph API rejected the mutation.")]
    if error.get("code") is not None:
        details.append(f"code={error['code']}")
    if error.get("error_subcode") is not None:
        details.append(f"subcode={error['error_subcode']}")
    if error.get("fbtrace_id"):
        details.append(f"trace={error['fbtrace_id']}")
    return "; ".join(details)


def _require_dataset_account(dataset: dict[str, Any], account_id: str) -> None:
    shared = {_account_id(str(value)) for value in dataset.get("shared_accounts", [])}
    if account_id not in shared:
        raise MutationError("Conversion dataset is not linked to the authorized ad account.")
