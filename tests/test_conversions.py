import json

import pytest

from meta_ads_mcp.conversions import ConversionTestEventDraft, DatasetDraft
from meta_ads_mcp.gateway import MetaAdsGateway, MutationError


class FakeApi:
    def __init__(self): self.posts = []
    def get_ad_account(self, account_id): return {"id": account_id, "business": {"id": "9"}}
    def list_conversion_datasets(self, account_id): return [{"id": "111", "name": "Test", "account_id": account_id}]
    def get_conversion_dataset(self, dataset_id): return {"id": dataset_id, "name": "Test", "shared_accounts": ["act_123"], "owner_business": {"id": "9"}}
    def post(self, path, params): self.posts.append((path, params)); return {"id": "222"} if path.endswith("/adspixels") else {"events_received": 1, "fbtrace_id": "trace"}


def test_lists_datasets_scoped_to_account():
    assert MetaAdsGateway(FakeApi()).list_conversion_datasets("123")[0]["id"] == "111"


def test_creates_and_links_dataset_to_authorized_account():
    api = FakeApi(); gateway = MetaAdsGateway(api)
    result = gateway.create_conversion_dataset("123", "9", DatasetDraft(name="Canario"))
    assert result == {"id": "222", "name": "Canario", "account_id": "act_123", "business_id": "9"}
    assert api.posts == [("/9/adspixels", {"name": "Canario", "is_crm": True}), ("/222/shared_accounts", {"account_id": "act_123", "business": "9"})]


def test_test_event_requires_dataset_link_and_test_code():
    api = FakeApi(); gateway = MetaAdsGateway(api)
    draft = ConversionTestEventDraft(test_event_code="TEST123", event={"event_name":"Contact","event_time":1700000000,"event_id":"synthetic-1","action_source":"business_messaging","messaging_channel":"whatsapp","user_data":{"external_id":["hash"]}})
    result = gateway.send_test_conversion_event("123", "111", draft)
    assert result["events_received"] == 1
    assert api.posts[-1][0] == "/111/events"
    assert api.posts[-1][1]["test_event_code"] == "TEST123"
    assert json.loads(api.posts[-1][1]["data"])[0]["messaging_channel"] == "whatsapp"


def test_business_messaging_event_requires_channel():
    with pytest.raises(ValueError, match="messaging_channel is required"):
        ConversionTestEventDraft(test_event_code="TEST123", event={"event_name":"Contact","event_time":1700000000,"event_id":"synthetic-1","action_source":"business_messaging","user_data":{"external_id":["hash"]}})


def test_test_event_refuses_dataset_from_another_account():
    api = FakeApi()
    api.get_conversion_dataset = lambda _: {"id":"111","shared_accounts":["act_999"]}
    with pytest.raises(MutationError, match="not linked"):
        MetaAdsGateway(api).send_test_conversion_event("123", "111", ConversionTestEventDraft(test_event_code="TEST123", event={"event_name":"Contact","event_time":1700000000,"event_id":"synthetic-1","action_source":"business_messaging","messaging_channel":"whatsapp","user_data":{"external_id":["hash"]}}))


def test_dataset_creation_refuses_unrelated_business():
    api = FakeApi()
    with pytest.raises(MutationError, match="does not own"):
        MetaAdsGateway(api).create_conversion_dataset("123", "8", DatasetDraft(name="Canario"))
    assert api.posts == []
