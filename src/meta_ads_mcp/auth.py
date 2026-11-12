from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken


TOKEN_PATTERN = re.compile(r"^mcp_([A-Za-z0-9_-]{8,32})\.([A-Za-z0-9_-]{32,})$")


@dataclass(frozen=True)
class ApiKeyIdentity:
    key_id: str
    user_id: str
    user_name: str
    token_hmac: str
    user_active: bool
    key_active: bool
    expires_at: datetime | None


@dataclass(frozen=True)
class AccessGrant:
    scope_type: Literal["account", "business", "all"]
    scope_id: str
    access_level: Literal["read", "write"]


@dataclass(frozen=True)
class Actor:
    user_id: str
    user_name: str
    key_id: str


class TokenRepository(Protocol):
    async def find_api_key(self, token_prefix: str) -> ApiKeyIdentity | None: ...
    async def mark_api_key_used(self, key_id: str) -> None: ...


class PostgresTokenVerifier:
    def __init__(self, repository: TokenRepository, pepper: str):
        self._repository = repository
        self._pepper = pepper.encode()

    async def verify_token(self, token: str) -> AccessToken | None:
        match = TOKEN_PATTERN.fullmatch(token)
        if not match:
            return None
        identity = await self._repository.find_api_key(match.group(1))
        if identity is None or not identity.user_active or not identity.key_active:
            return None
        if identity.expires_at and identity.expires_at <= datetime.now(UTC):
            return None
        digest = hmac.new(self._pepper, token.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(identity.token_hmac, digest):
            return None
        await self._repository.mark_api_key_used(identity.key_id)
        return AccessToken(token=token, client_id=f"mcp-key:{match.group(1)}", scopes=["mcp"], subject=identity.user_id, claims={"key_id": identity.key_id, "user_name": identity.user_name})


class AuthorizationService:
    def __init__(self, repository, token_supplier=get_access_token):
        self._repository = repository
        self._token_supplier = token_supplier

    def current_actor(self) -> Actor:
        token = self._token_supplier()
        if token is None or token.subject is None:
            raise PermissionError("Authenticated MCP user is required.")
        claims = token.claims or {}
        if not isinstance(claims.get("key_id"), str) or not isinstance(claims.get("user_name"), str):
            raise PermissionError("Authenticated MCP identity is incomplete.")
        return Actor(token.subject, claims["user_name"], claims["key_id"])

    def require(self, account_id: str, level: Literal["read", "write"] = "read") -> Actor:
        actor = self.current_actor()
        account = _digits(account_id)
        business_ids = set(self._repository.business_ids_for_account(account))
        grants = self._repository.list_access_grants(actor.user_id)
        if not any(_allows(grant, account, business_ids, level) for grant in grants):
            raise PermissionError(f"User is not authorized for {level} access to account {account}.")
        return actor

    def filter_accounts(self, rows: list[dict], level: Literal["read", "write"] = "read") -> list[dict]:
        actor = self.current_actor()
        grants = self._repository.list_access_grants(actor.user_id)
        output = []
        for row in rows:
            account = _digits(str(row.get("id") or row.get("account_id") or ""))
            businesses = set(map(str, row.get("business_ids", []))) or set(self._repository.business_ids_for_account(account))
            if any(_allows(grant, account, businesses, level) for grant in grants):
                output.append(row)
        return output


def _allows(grant: AccessGrant, account: str, businesses: set[str], required: str) -> bool:
    scope_matches = (
        grant.scope_type == "all"
        or (grant.scope_type == "account" and _digits(grant.scope_id) == account)
        or (grant.scope_type == "business" and grant.scope_id in businesses)
    )
    return scope_matches and (required == "read" or grant.access_level == "write")


def _digits(value: str) -> str:
    return value.lower().removeprefix("act_").replace("-", "")
