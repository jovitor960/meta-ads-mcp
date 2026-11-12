import json

import pytest
from pydantic import ValidationError

from meta_ads_mcp.campaigns import (
    LinkCreativeSpec,
    AdImageUploadDraft,
    PausedAdDraft,
    PausedAdSetUpdate,
    PausedAdSetDraft,
    PausedAdUpdate,
    PausedCampaignDraft,
    PausedCampaignUpdate,
)
from meta_ads_mcp.gateway import MetaAdsGateway, MutationError


class FakeMutationApi:
    def __init__(self):
        self.posts = []
        self.objects = {}
        self.uploads = []

    def post(self, path, params):
        self.posts.append((path, params))
        return {"id": "campaign-1"}

    def get_object(self, object_id, fields):
        return self.objects[object_id]

    def upload_image(self, account_id, image_url, filename=None):
        self.uploads.append((account_id, image_url, filename))
        return {"images": {"uploaded.png": {"hash": "hash-123"}}}


def test_create_campaign_forces_paused_status_and_applies_budget_cap():
    api = FakeMutationApi()
    gateway = MetaAdsGateway(api, max_budget_minor=500_000)

    result = gateway.create_paused_campaign(
        "123",
        PausedCampaignDraft(
            name="Campanha teste",
            objective="OUTCOME_TRAFFIC",
            daily_budget_minor=250_000,
        ),
    )

    assert result == {"id": "campaign-1", "status": "PAUSED"}
    assert api.posts == [
        (
            "/act_123/campaigns",
            {
                "name": "Campanha teste",
                "objective": "OUTCOME_TRAFFIC",
                "buying_type": "AUCTION",
                "special_ad_categories": "[]",
                "is_adset_budget_sharing_enabled": "false",
                "daily_budget": 250_000,
                "status": "PAUSED",
            },
        )
    ]


def test_upload_ad_image_returns_meta_image_hash():
    api = FakeMutationApi()
    gateway = MetaAdsGateway(api)

    result = gateway.upload_ad_image(
        "123",
        AdImageUploadDraft(
            image_url="https://cdn.example.com/uploaded.png",
            filename="uploaded.png",
        ),
    )

    assert result == {"images": {"uploaded.png": {"hash": "hash-123"}}}
    assert api.uploads == [("act_123", "https://cdn.example.com/uploaded.png", "uploaded.png")]


def test_create_campaign_rejects_budget_above_configured_cap():
    gateway = MetaAdsGateway(FakeMutationApi(), max_budget_minor=500_000)

    with pytest.raises(MutationError, match="configured maximum"):
        gateway.create_paused_campaign(
            "123",
            PausedCampaignDraft(
                name="Muito cara",
                objective="OUTCOME_TRAFFIC",
                daily_budget_minor=500_001,
            ),
        )


def test_create_adset_requires_paused_parent_and_forces_paused_status():
    api = FakeMutationApi()
    api.objects["111"] = {
        "id": "111",
        "account_id": "123",
        "status": "PAUSED",
    }
    gateway = MetaAdsGateway(api, max_budget_minor=500_000)

    result = gateway.create_paused_adset(
        "act_123",
        PausedAdSetDraft(
            name="Conjunto Brasília",
            campaign_id="111",
            daily_budget_minor=10_000,
            optimization_goal="LINK_CLICKS",
            targeting={"geo_locations": {"countries": ["BR"]}},
        ),
    )

    assert result == {"id": "campaign-1", "status": "PAUSED"}
    path, params = api.posts[0]
    assert path == "/act_123/adsets"
    assert params["status"] == "PAUSED"
    assert params["campaign_id"] == "111"
    assert params["daily_budget"] == 10_000


@pytest.mark.parametrize("phone_id_field", ["whats_app_business_phone_number_id", "whatsapp_phone_number_id"])
def test_create_whatsapp_adset_sends_meta_phone_id_field(phone_id_field):
    api = FakeMutationApi()
    api.objects["111"] = {"id": "111", "account_id": "123", "status": "PAUSED", "objective": "OUTCOME_LEADS"}
    gateway = MetaAdsGateway(api, max_budget_minor=500_000)

    gateway.create_paused_adset(
        "123",
        PausedAdSetDraft(
            name="WhatsApp Brasilia",
            campaign_id="111",
            optimization_goal="CONVERSATIONS",
            daily_budget_minor=500,
            targeting={
                "geo_locations": {
                    "custom_locations": [
                        {"latitude": -15.79, "longitude": -47.88, "radius": 10, "distance_unit": "kilometer"}
                    ]
                },
                "age_min": 25,
                "age_max": 55,
                "publisher_platforms": ["facebook", "instagram"],
                "facebook_positions": ["feed"],
                "instagram_positions": ["stream"],
            },
            destination_type="WHATSAPP",
            promoted_object={"page_id": "333", phone_id_field: "444"},
        ),
    )

    params = api.posts[0][1]
    assert params["destination_type"] == "WHATSAPP"
    assert json.loads(params["promoted_object"]) == {
        "page_id": "333",
        "whats_app_business_phone_number_id": "444",
    }


@pytest.mark.parametrize("phone_number", ["5561999999999", "+55 (61) 99999-9999"])
def test_whatsapp_adset_can_select_a_phone_number_without_requiring_a_phone_id(phone_number):
    api = FakeMutationApi()
    api.objects["111"] = {"id": "111", "account_id": "123", "status": "PAUSED", "objective": "OUTCOME_LEADS"}
    gateway = MetaAdsGateway(api, max_budget_minor=500_000)

    gateway.create_paused_adset(
        "123",
        PausedAdSetDraft(
            name="WhatsApp por telefone",
            campaign_id="111",
            optimization_goal="CONVERSATIONS",
            daily_budget_minor=500,
            targeting={"geo_locations": {"countries": ["BR"]}},
            destination_type="WHATSAPP",
            promoted_object={"page_id": "333", "whatsapp_phone_number": phone_number},
        ),
    )

    assert json.loads(api.posts[0][1]["promoted_object"]) == {
        "page_id": "333",
        "whatsapp_phone_number": "5561999999999",
    }
    assert api.posts[0][1]["status"] == "PAUSED"


@pytest.mark.parametrize("field", ["page_id", "whats_app_business_phone_number_id", "whatsapp_phone_number_id"])
def test_whatsapp_adset_rejects_a_formatted_phone_number_in_an_id_field(field):
    promoted = {"page_id": "333", "whats_app_business_phone_number_id": "444"}
    if field == "whatsapp_phone_number_id":
        del promoted["whats_app_business_phone_number_id"]
    promoted[field] = "+55 (61) 99999-9999"

    with pytest.raises(ValidationError, match="numeric Meta ID"):
        PausedAdSetDraft(
            name="Telefone no campo de ID",
            campaign_id="111",
            optimization_goal="CONVERSATIONS",
            daily_budget_minor=500,
            targeting={"geo_locations": {"countries": ["BR"]}},
            destination_type="WHATSAPP",
            promoted_object=promoted,
        )


def test_conversations_adset_requires_a_destination():
    with pytest.raises(ValidationError, match="destination_type"):
        PausedAdSetDraft(
            name="Conversas sem destino",
            campaign_id="111",
            optimization_goal="CONVERSATIONS",
            daily_budget_minor=500,
            targeting={"geo_locations": {"countries": ["BR"]}},
        )


@pytest.mark.parametrize(
    "promoted, message",
    [
        ({"page_id": "333"}, "either"),
        ({"whatsapp_phone_number": "5561999999999"}, "page_id"),
        ({"page_id": None, "whatsapp_phone_number": "5561999999999"}, "numeric Meta ID"),
        ({"page_id": "333", "whats_app_business_phone_number_id": ""}, "numeric Meta ID"),
        ({"page_id": "333", "whats_app_business_phone_number_id": True}, "numeric Meta ID"),
        ({"page_id": "333", "whatsapp_phone_number": "abc5561999999999"}, "country code"),
        ({"page_id": "333", "whatsapp_phone_number": "333"}, "country code"),
        ({"page_id": "333", "whatsapp_phone_number": 5561999999999}, "phone number string"),
        ({"page_id": "333", "whats_app_business_phone_number_id": "444", "whatsapp_phone_number_id": "555"}, "Conflicting"),
    ],
)
def test_whatsapp_adset_rejects_missing_invalid_or_ambiguous_destinations(promoted, message):
    with pytest.raises(ValidationError, match=message):
        PausedAdSetDraft(
            name="Destino WhatsApp invalido",
            campaign_id="111",
            optimization_goal="CONVERSATIONS",
            daily_budget_minor=500,
            targeting={"geo_locations": {"countries": ["BR"]}},
            destination_type="WHATSAPP",
            promoted_object=promoted,
        )


def test_whatsapp_adset_validation_preserves_distinct_id_and_phone_number():
    api = FakeMutationApi()
    api.objects["111"] = {"id": "111", "account_id": "123", "status": "PAUSED", "objective": "OUTCOME_LEADS"}
    gateway = MetaAdsGateway(api, max_budget_minor=500_000)
    promoted = {
        "page_id": 333,
        "whats_app_business_phone_number_id": "444",
        "whatsapp_phone_number_id": 444,
        "whatsapp_phone_number": "+55 (61) 99999-9999",
    }

    result = gateway.validate_paused_adset(
        "123",
        PausedAdSetDraft(
            name="WhatsApp com ID e telefone",
            campaign_id="111",
            optimization_goal="CONVERSATIONS",
            daily_budget_minor=500,
            targeting={"geo_locations": {"countries": ["BR"]}},
            destination_type="WHATSAPP",
            promoted_object=promoted,
        ),
    )

    assert json.loads(result["request"]["promoted_object"]) == {
        "page_id": "333",
        "whats_app_business_phone_number_id": "444",
        "whatsapp_phone_number": "5561999999999",
    }
    assert promoted["whatsapp_phone_number"] == "+55 (61) 99999-9999"
    assert promoted["whatsapp_phone_number_id"] == 444
    assert api.posts == []


def test_adset_creation_requires_exactly_one_budget():
    with pytest.raises(ValidationError, match="exactly one budget"):
        PausedAdSetDraft(
            name="Sem orÃ§amento",
            campaign_id="111",
            optimization_goal="LINK_CLICKS",
            targeting={"geo_locations": {"countries": ["BR"]}},
        )


def test_conversations_rejects_incompatible_campaign_objective():
    api = FakeMutationApi()
    api.objects["111"] = {"id": "111", "account_id": "123", "status": "PAUSED", "objective": "OUTCOME_TRAFFIC"}
    gateway = MetaAdsGateway(api, max_budget_minor=500_000)

    with pytest.raises(MutationError, match="CONVERSATIONS.*OUTCOME_TRAFFIC"):
        gateway.create_paused_adset(
            "123",
            PausedAdSetDraft(
                name="Conversas incompatÃ­veis",
                campaign_id="111",
                optimization_goal="CONVERSATIONS",
                daily_budget_minor=500,
                destination_type="WHATSAPP",
                promoted_object={"page_id": "333", "whatsapp_phone_number_id": "444"},
                targeting={"geo_locations": {"countries": ["BR"]}},
            ),
        )

    assert api.posts == []


def test_create_adset_refuses_archived_campaign():
    api = FakeMutationApi()
    api.objects["111"] = {
        "id": "111",
        "account_id": "123",
        "status": "ARCHIVED",
    }
    gateway = MetaAdsGateway(api, max_budget_minor=500_000)

    with pytest.raises(MutationError, match="campaign must be ACTIVE or PAUSED"):
        gateway.create_paused_adset(
            "123",
            PausedAdSetDraft(
                name="Bloqueado",
                campaign_id="111",
                optimization_goal="LINK_CLICKS",
                daily_budget_minor=500,
                targeting={"geo_locations": {"countries": ["BR"]}},
            ),
        )

    assert api.posts == []


def test_update_campaign_changes_only_paused_campaign_and_keeps_it_paused():
    api = FakeMutationApi()
    api.objects["111"] = {"id": "111", "account_id": "123", "status": "PAUSED"}
    gateway = MetaAdsGateway(api, max_budget_minor=500_000)

    result = gateway.update_paused_campaign(
        "123",
        PausedCampaignUpdate(
            campaign_id="111",
            name="Campanha atualizada",
            daily_budget_minor=25_000,
        ),
    )

    assert result["status"] == "PAUSED"
    assert api.posts == [
        (
            "/111",
            {
                "name": "Campanha atualizada",
                "daily_budget": 25_000,
                "status": "PAUSED",
            },
        )
    ]


def test_create_ad_with_inline_creative_forces_paused():
    api = FakeMutationApi()
    api.objects["222"] = {
        "id": "222",
        "account_id": "123",
        "campaign_id": "111",
        "status": "PAUSED",
    }
    api.objects["111"] = {
        "id": "111",
        "account_id": "123",
        "status": "PAUSED",
    }
    gateway = MetaAdsGateway(api, max_budget_minor=500_000)

    result = gateway.create_paused_ad(
        "123",
        PausedAdDraft(
            name="Anúncio teste",
            adset_id="222",
            creative_spec=LinkCreativeSpec(
                page_id="333",
                link_url="https://example.com/oferta",
                message="Conheça nossa oferta",
                headline="Oferta especial",
                image_hash="abc123",
                call_to_action_type="LEARN_MORE",
            ),
        ),
    )

    assert result == {"id": "campaign-1", "status": "PAUSED"}
    path, params = api.posts[0]
    assert path == "/act_123/ads"
    assert params["status"] == "PAUSED"
    assert params["adset_id"] == "222"
    assert '"page_id": "333"' in params["creative"]
    assert '"image_hash": "abc123"' in params["creative"]


@pytest.mark.parametrize("instagram_field", ["instagram_actor_id", "instagram_user_id"])
@pytest.mark.parametrize("operation", ["create_paused_ad", "validate_paused_ad", "update_paused_ad", "validate_paused_ad_update"])
def test_ad_operations_send_instagram_identity_as_meta_user_id(instagram_field, operation):
    api = FakeMutationApi()
    api.objects["222"] = {"id": "222", "account_id": "123", "campaign_id": "111", "status": "PAUSED"}
    api.objects["111"] = {"id": "111", "account_id": "123", "status": "PAUSED"}
    api.objects["555"] = {"id": "555", "account_id": "123", "status": "PAUSED"}
    gateway = MetaAdsGateway(api)
    creative = LinkCreativeSpec(
        page_id="333",
        **{instagram_field: "17841402054874577"},
        image_hash="image-test",
        link_url="https://api.whatsapp.com/send?phone=556199636222",
        message="Consulte pneus pelo WhatsApp",
        call_to_action_type="WHATSAPP_MESSAGE",
    )
    if operation in {"create_paused_ad", "validate_paused_ad"}:
        draft = PausedAdDraft(name="WhatsApp com identidade Instagram", adset_id="222", creative_spec=creative)
    else:
        draft = PausedAdUpdate(ad_id="555", creative_spec=creative)
    result = getattr(gateway, operation)("123", draft)
    if operation.startswith("validate_"):
        request = result["request"]
        assert api.posts == []
        assert result["forced_status"] == "PAUSED"
    else:
        request = api.posts[0][1]
        assert result["status"] == "PAUSED"

    story = json.loads(request["creative"])["object_story_spec"]
    assert story["instagram_user_id"] == "17841402054874577"
    assert "instagram_actor_id" not in story
    assert story["page_id"] == "333"
    assert story["link_data"]["call_to_action"]["type"] == "WHATSAPP_MESSAGE"
    assert request["status"] == "PAUSED"


def test_creative_rejects_conflicting_instagram_identity_aliases():
    with pytest.raises(ValidationError, match="Conflicting Instagram IDs"):
        LinkCreativeSpec(
            page_id="333",
            instagram_user_id="444",
            instagram_actor_id="555",
            link_url="https://example.com/oferta",
            message="Oferta",
            image_hash="image-test",
        )


def test_creative_accepts_matching_instagram_identity_aliases():
    api = FakeMutationApi()
    api.objects["222"] = {"id": "222", "account_id": "123", "campaign_id": "111", "status": "PAUSED"}
    api.objects["111"] = {"id": "111", "account_id": "123", "status": "PAUSED"}
    result = MetaAdsGateway(api).validate_paused_ad(
        "123",
        PausedAdDraft(
            name="Identidade Instagram consistente",
            adset_id="222",
            creative_spec=LinkCreativeSpec(
                page_id="333",
                instagram_user_id="444",
                instagram_actor_id="444",
                link_url="https://example.com/oferta",
                message="Oferta",
                image_hash="image-test",
            ),
        ),
    )
    story = json.loads(result["request"]["creative"])["object_story_spec"]
    assert story["instagram_user_id"] == "444"
    assert "instagram_actor_id" not in story
    assert api.posts == []


def test_create_ad_refuses_archived_adset():
    api = FakeMutationApi()
    api.objects["222"] = {
        "id": "222",
        "account_id": "123",
        "campaign_id": "111",
        "status": "ARCHIVED",
    }
    gateway = MetaAdsGateway(api)

    with pytest.raises(MutationError, match="ad set must be ACTIVE or PAUSED"):
        gateway.create_paused_ad(
            "123",
            PausedAdDraft(name="Bloqueado", adset_id="222", creative_id="444"),
        )

    assert api.posts == []


def test_update_adset_changes_only_paused_resource_and_keeps_it_paused():
    api = FakeMutationApi()
    api.objects["222"] = {
        "id": "222",
        "account_id": "123",
        "status": "PAUSED",
    }
    gateway = MetaAdsGateway(api, max_budget_minor=500_000)

    result = gateway.update_paused_adset(
        "123",
        PausedAdSetUpdate(
            adset_id="222",
            name="Conjunto otimizado",
            daily_budget_minor=12_000,
            targeting={"geo_locations": {"countries": ["BR"]}},
        ),
    )

    assert result["status"] == "PAUSED"
    assert api.posts == [
        (
            "/222",
            {
                "name": "Conjunto otimizado",
                "daily_budget": 12_000,
                "targeting": '{"geo_locations": {"countries": ["BR"]}}',
                "status": "PAUSED",
            },
        )
    ]


def test_update_adset_refuses_active_resource():
    api = FakeMutationApi()
    api.objects["222"] = {"id": "222", "account_id": "123", "status": "ACTIVE"}

    with pytest.raises(MutationError, match="ad set must be PAUSED"):
        MetaAdsGateway(api).update_paused_adset(
            "123", PausedAdSetUpdate(adset_id="222", name="Não alterar")
        )

    assert api.posts == []


def test_update_ad_content_changes_only_paused_ad_and_keeps_it_paused():
    api = FakeMutationApi()
    api.objects["555"] = {"id": "555", "account_id": "123", "status": "PAUSED"}
    gateway = MetaAdsGateway(api)

    result = gateway.update_paused_ad(
        "123",
        PausedAdUpdate(
            ad_id="555",
            name="Novo anúncio",
            creative_id="444",
        ),
    )

    assert result["status"] == "PAUSED"
    assert api.posts == [
        (
            "/555",
            {
                "name": "Novo anúncio",
                "creative": '{"creative_id": "444"}',
                "status": "PAUSED",
            },
        )
    ]


def test_update_ad_refuses_active_resource():
    api = FakeMutationApi()
    api.objects["555"] = {"id": "555", "account_id": "123", "status": "ACTIVE"}

    with pytest.raises(MutationError, match="ad must be PAUSED"):
        MetaAdsGateway(api).update_paused_ad(
            "123", PausedAdUpdate(ad_id="555", name="Não alterar")
        )

    assert api.posts == []
