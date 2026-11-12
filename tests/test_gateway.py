import pytest
import requests

from meta_ads_mcp.gateway import MetaAdsGateway, MetaGraphApi, MutationError, QueryError


class FakeApi:
    def __init__(self):
        self.insight_calls = []

    def list_ad_accounts(self, business_id):
        return [{"id": "act_123", "name": "Client"}]

    def get_ad_account(self, account_id):
        return {"id": account_id}

    def list_campaigns(self, account_id, max_rows):
        return [{"id": "campaign-1", "account_id": account_id}]

    def get_insights(self, account_id, level, date_preset, max_rows):
        self.insight_calls.append((account_id, level, date_preset, max_rows))
        return [{"spend": "10.00"}]

    def list_adsets(self, account_id, campaign_id, max_rows):
        return [{"id": "adset-1", "account_id": account_id, "campaign_id": campaign_id}]

    def list_ads(self, account_id, adset_id, max_rows):
        return [{"id": "ad-1", "account_id": account_id, "adset_id": adset_id}]

    def list_ad_creatives(self, account_id, max_rows):
        return [{"id": "creative-1", "account_id": account_id}]

    def get_object(self, object_id, fields):
        return {"id": object_id, "account_id": "123"}


def test_campaign_insights_normalizes_account_and_limits_rows():
    api = FakeApi()
    gateway = MetaAdsGateway(api=api, business_id="999")

    result = gateway.get_insights(
        account_id="123-456",
        level="campaign",
        date_preset="last_30d",
        max_rows=250,
    )

    assert result == [{"spend": "10.00"}]
    assert api.insight_calls == [("act_123456", "campaign", "last_30d", 250)]


def test_insights_rejects_unsupported_level():
    gateway = MetaAdsGateway(api=FakeApi(), business_id=None)

    with pytest.raises(QueryError, match="Unsupported insight level"):
        gateway.get_insights("act_123", "creative", "last_30d", 100)


def test_insights_rejects_unbounded_result_size():
    gateway = MetaAdsGateway(api=FakeApi(), business_id=None)

    with pytest.raises(QueryError, match="between 1 and 500"):
        gateway.get_insights("act_123", "account", "last_30d", 501)


def test_graph_adapter_posts_form_data_with_token_and_appsecret_proof(monkeypatch):
    captured = {}

    class TokenSource:
        def get(self):
            return "oauth-token"

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "new-id"}

    def fake_post(url, data, timeout):
        captured.update(url=url, data=data, timeout=timeout)
        return Response()

    monkeypatch.setattr("meta_ads_mcp.gateway.requests.post", fake_post)
    api = MetaGraphApi(token_source=TokenSource(), app_secret="app-secret")

    result = api.post("/act_123/campaigns", {"name": "Teste", "status": "PAUSED"})

    assert result == {"id": "new-id"}
    assert captured["url"].endswith("/act_123/campaigns")
    assert captured["data"]["status"] == "PAUSED"
    assert captured["data"]["access_token"] == "oauth-token"
    assert len(captured["data"]["appsecret_proof"]) == 64


def test_graph_adapter_exposes_safe_meta_error_details(monkeypatch):
    class TokenSource:
        def get(self):
            return "secret-oauth-token"

    class Response:
        def raise_for_status(self):
            raise requests.HTTPError("400 Client Error")

        def json(self):
            return {
                "error": {
                    "message": "Required parameter is missing",
                    "type": "OAuthException",
                    "code": 100,
                    "error_subcode": 18157520,
                    "fbtrace_id": "trace-123",
                }
            }

    monkeypatch.setattr("meta_ads_mcp.gateway.requests.post", lambda *args, **kwargs: Response())
    api = MetaGraphApi(token_source=TokenSource(), app_secret="app-secret")

    with pytest.raises(MutationError) as error:
        api.post("/act_123/campaigns", {"name": "Teste"})

    message = str(error.value)
    assert "Required parameter is missing" in message
    assert "code=100" in message
    assert "subcode=18157520" in message
    assert "trace=trace-123" in message
    assert "secret-oauth-token" not in message


def test_gateway_lists_adsets_ads_and_creatives_for_account():
    gateway = MetaAdsGateway(FakeApi())

    assert gateway.list_adsets("123", campaign_id="111")[0]["id"] == "adset-1"
    assert gateway.list_ads("123", adset_id="222")[0]["id"] == "ad-1"
    assert gateway.list_ad_creatives("123")[0]["id"] == "creative-1"


def test_graph_reads_raise_sanitized_errors(monkeypatch):
    class TokenSource:
        def get(self): return "secret-oauth-token"
    class Response:
        def raise_for_status(self): raise requests.HTTPError("bad https://graph.test/x?access_token=secret-oauth-token")
        def json(self): return {"error": {"message": "Denied", "type": "OAuthException", "code": 190}}
    monkeypatch.setattr("meta_ads_mcp.gateway.requests.get", lambda *args, **kwargs: Response())
    api = MetaGraphApi(token_source=TokenSource(), app_secret="app-secret")
    with pytest.raises(QueryError) as error:
        api.get_object("123", "id")
    assert "Denied" in str(error.value)
    assert "secret-oauth-token" not in str(error.value)
