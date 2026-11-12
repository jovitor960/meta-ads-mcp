from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlencode

import requests


class InvalidOAuthState(ValueError):
    pass


class OAuthRepository(Protocol):
    def save_oauth_state(self, state_hash: str, expires_at: datetime) -> None: ...
    def consume_oauth_state(self, state_hash: str) -> bool: ...
    def upsert_oauth_connection(self, **kwargs: Any) -> None: ...
    def replace_inventory(self, businesses: list[dict[str, Any]], accounts: list[dict[str, Any]]) -> None: ...


class MetaOAuthService:
    def __init__(self, *, app_id: str, redirect_uri: str, scopes: tuple[str, ...], repository: OAuthRepository, client, cipher):
        self.app_id = app_id
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self.repository = repository
        self.client = client
        self.cipher = cipher

    def authorization_url(self) -> str:
        state = secrets.token_urlsafe(32)
        self.repository.save_oauth_state(_hash(state), datetime.now(UTC) + timedelta(minutes=10))
        query = urlencode({"client_id": self.app_id, "redirect_uri": self.redirect_uri, "state": state, "scope": ",".join(self.scopes), "response_type": "code"})
        graph_version = getattr(self.client, "graph_version", "v26.0")
        return f"https://www.facebook.com/{graph_version}/dialog/oauth?{query}"

    def complete(self, *, code: str, state: str) -> dict[str, Any]:
        if not self.repository.consume_oauth_state(_hash(state)):
            raise InvalidOAuthState("OAuth state is invalid, expired, or already used.")
        short = self.client.exchange_code(code, self.redirect_uri)
        long_lived = self.client.exchange_long_lived_token(short["access_token"])
        token = long_lived["access_token"]
        identity = self.client.get_identity(token)
        businesses, accounts = self.client.discover_inventory(token)
        expires_at = None
        if long_lived.get("expires_in"):
            expires_at = datetime.now(UTC) + timedelta(seconds=int(long_lived["expires_in"]))
        granted_scopes = self.client.get_granted_scopes(token)
        self.repository.upsert_oauth_connection(meta_user_id=str(identity["id"]), meta_user_name=identity.get("name") or str(identity["id"]), encrypted_access_token=self.cipher.encrypt(token), granted_scopes=granted_scopes, expires_at=expires_at)
        self.repository.replace_inventory(businesses, accounts)
        return {"meta_user_id": str(identity["id"]), "name": identity.get("name") or str(identity["id"]), "businesses": len(businesses), "ad_accounts": len(accounts)}


class MetaGraphOAuthClient:
    def __init__(self, app_id: str, app_secret: str, graph_version: str = "v26.0", timeout: int = 30):
        self.app_id = app_id
        self.app_secret = app_secret
        self.graph_version = graph_version
        self.base_url = f"https://graph.facebook.com/{graph_version}"
        self.timeout = timeout

    def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        return self._get("/oauth/access_token", {"client_id": self.app_id, "client_secret": self.app_secret, "redirect_uri": redirect_uri, "code": code})

    def exchange_long_lived_token(self, token: str) -> dict[str, Any]:
        return self._get("/oauth/access_token", {"grant_type": "fb_exchange_token", "client_id": self.app_id, "client_secret": self.app_secret, "fb_exchange_token": token})

    def get_identity(self, token: str) -> dict[str, Any]:
        return self._graph_get("/me", token, {"fields": "id,name"})

    def get_granted_scopes(self, token: str) -> list[str]:
        rows = self._all("/me/permissions", token, {"limit": 500})
        return sorted(str(row["permission"]) for row in rows if row.get("status") == "granted" and row.get("permission"))

    def discover_inventory(self, token: str):
        businesses = self._all("/me/businesses", token, {"fields": "id,name", "limit": 500})
        accounts: dict[str, dict[str, Any]] = {}
        direct = self._all("/me/adaccounts", token, {"fields": "id,account_id,name,account_status,currency,timezone_name", "limit": 500})
        for account in direct:
            account["business_ids"] = []
            accounts[account["id"]] = account
        for business in businesses:
            for edge in ("owned_ad_accounts", "client_ad_accounts"):
                for account in self._all(f"/{business['id']}/{edge}", token, {"fields": "id,account_id,name,account_status,currency,timezone_name", "limit": 500}):
                    existing = accounts.setdefault(account["id"], account)
                    existing.setdefault("business_ids", [])
                    if str(business["id"]) not in existing["business_ids"]:
                        existing["business_ids"].append(str(business["id"]))
        return businesses, list(accounts.values())

    def _graph_get(self, path: str, token: str, params: dict[str, Any]) -> dict[str, Any]:
        params = {**params, "access_token": token, "appsecret_proof": hmac.new(self.app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()}
        return self._get(path, params)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = requests.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _all(self, path: str, token: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        payload = self._graph_get(path, token, params)
        rows = list(payload.get("data", []))
        while payload.get("paging", {}).get("next") and len(rows) < 5000:
            response = requests.get(payload["paging"]["next"], timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            rows.extend(payload.get("data", []))
        return rows[:5000]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
