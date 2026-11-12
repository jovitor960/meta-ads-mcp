from mcp.server.auth.provider import AccessToken

from meta_ads_mcp.auth import AccessGrant, AuthorizationService


class FakeRepository:
    def __init__(self, grants):
        self.grants = grants

    def list_access_grants(self, user_id):
        return self.grants

    def business_ids_for_account(self, account_id):
        return []


def _token():
    return AccessToken(
        token="test",
        client_id="test",
        scopes=["mcp"],
        subject="user-1",
        claims={"key_id": "key-1", "user_name": "Joao"},
    )


def test_all_read_grant_includes_current_and_future_accounts():
    service = AuthorizationService(
        FakeRepository([AccessGrant("all", "*", "read")]),
        token_supplier=_token,
    )

    rows = service.filter_accounts(
        [{"id": "act_123"}, {"id": "act_999", "business_ids": ["future"]}]
    )

    assert [row["id"] for row in rows] == ["act_123", "act_999"]
    assert service.require("act_777", "read").user_id == "user-1"


def test_all_read_grant_does_not_allow_writes():
    service = AuthorizationService(
        FakeRepository([AccessGrant("all", "*", "read")]),
        token_supplier=_token,
    )

    try:
        service.require("act_123", "write")
    except PermissionError:
        pass
    else:
        raise AssertionError("read grant must not authorize write")
