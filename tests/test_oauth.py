from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from meta_ads_mcp.oauth import InvalidOAuthState, MetaOAuthService


class FakeRepository:
    def __init__(self):
        self.states = set()
        self.connection = None
        self.inventory = None

    def save_oauth_state(self, state_hash, expires_at):
        self.states.add(state_hash)

    def consume_oauth_state(self, state_hash):
        if state_hash not in self.states:
            return False
        self.states.remove(state_hash)
        return True

    def upsert_oauth_connection(self, **kwargs):
        self.connection = kwargs

    def replace_inventory(self, businesses, accounts):
        self.inventory = (businesses, accounts)


class FakeClient:
    def exchange_code(self, code, redirect_uri):
        assert code == "oauth-code"
        return {"access_token": "short-token"}

    def exchange_long_lived_token(self, token):
        assert token == "short-token"
        return {"access_token": "long-token", "expires_in": 5_184_000}

    def get_identity(self, token):
        return {"id": "42", "name": "Joao"}

    def discover_inventory(self, token):
        return ([{"id": "99", "name": "Agencia"}], [{"id": "act_123", "name": "Conta", "business_ids": ["99"]}])

    def get_granted_scopes(self, token):
        return ["ads_read"]


class FakeCipher:
    def encrypt(self, value):
        return "encrypted:" + value


def test_oauth_start_builds_exact_callback_and_persists_one_time_state():
    repository = FakeRepository()
    service = MetaOAuthService(
        app_id="123",
        redirect_uri="https://mcp.example/oauth/meta/callback",
        scopes=("ads_read", "business_management"),
        repository=repository,
        client=FakeClient(),
        cipher=FakeCipher(),
    )

    url = service.authorization_url()
    query = parse_qs(urlparse(url).query)

    assert query["client_id"] == ["123"]
    assert query["redirect_uri"] == ["https://mcp.example/oauth/meta/callback"]
    assert query["scope"] == ["ads_read,business_management"]
    assert len(repository.states) == 1


def test_oauth_callback_encrypts_long_lived_token_and_syncs_inventory():
    repository = FakeRepository()
    service = MetaOAuthService(
        app_id="123",
        redirect_uri="https://mcp.example/oauth/meta/callback",
        scopes=("ads_read",),
        repository=repository,
        client=FakeClient(),
        cipher=FakeCipher(),
    )
    url = service.authorization_url()
    state = parse_qs(urlparse(url).query)["state"][0]

    result = service.complete(code="oauth-code", state=state)

    assert result == {"meta_user_id": "42", "name": "Joao", "businesses": 1, "ad_accounts": 1}
    assert repository.connection["encrypted_access_token"] == "encrypted:long-token"
    assert repository.connection["expires_at"] > datetime.now(UTC) + timedelta(days=50)
    assert repository.connection["granted_scopes"] == ["ads_read"]
    assert repository.inventory[1][0]["id"] == "act_123"


def test_oauth_state_cannot_be_reused():
    repository = FakeRepository()
    service = MetaOAuthService(
        app_id="123",
        redirect_uri="https://mcp.example/oauth/meta/callback",
        scopes=("ads_read",),
        repository=repository,
        client=FakeClient(),
        cipher=FakeCipher(),
    )
    state = parse_qs(urlparse(service.authorization_url()).query)["state"][0]
    service.complete(code="oauth-code", state=state)
    with pytest.raises(InvalidOAuthState):
        service.complete(code="oauth-code", state=state)
