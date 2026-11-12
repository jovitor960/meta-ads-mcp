from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .auth import AccessGrant, ApiKeyIdentity


SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS meta_ads_mcp;

CREATE TABLE IF NOT EXISTS meta_ads_mcp.oauth_connections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    meta_user_id text NOT NULL UNIQUE,
    meta_user_name text NOT NULL,
    encrypted_access_token text NOT NULL,
    granted_scopes text[] NOT NULL DEFAULT '{}',
    expires_at timestamptz,
    active boolean NOT NULL DEFAULT true,
    connected_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS meta_ads_mcp.oauth_states (
    state_hash char(64) PRIMARY KEY,
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS meta_ads_mcp.businesses (
    business_id text PRIMARY KEY,
    connection_id uuid NOT NULL REFERENCES meta_ads_mcp.oauth_connections(id) ON DELETE CASCADE,
    name text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS meta_ads_mcp.ad_accounts (
    account_id text PRIMARY KEY,
    connection_id uuid NOT NULL REFERENCES meta_ads_mcp.oauth_connections(id) ON DELETE CASCADE,
    name text NOT NULL,
    currency text,
    account_status integer,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS meta_ads_mcp.ad_account_businesses (
    account_id text NOT NULL REFERENCES meta_ads_mcp.ad_accounts(account_id) ON DELETE CASCADE,
    business_id text NOT NULL REFERENCES meta_ads_mcp.businesses(business_id) ON DELETE CASCADE,
    PRIMARY KEY (account_id, business_id)
);

CREATE TABLE IF NOT EXISTS meta_ads_mcp.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL CHECK (length(trim(name)) > 0),
    email text NOT NULL CHECK (position('@' in email) > 1),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS meta_users_email_lower_uidx
    ON meta_ads_mcp.users ((lower(email)));

CREATE TABLE IF NOT EXISTS meta_ads_mcp.api_keys (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES meta_ads_mcp.users(id) ON DELETE CASCADE,
    device_name text NOT NULL,
    token_prefix text NOT NULL UNIQUE,
    token_hmac char(64) NOT NULL,
    active boolean NOT NULL DEFAULT true,
    expires_at timestamptz,
    last_used_at timestamptz,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS meta_ads_mcp.access_grants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES meta_ads_mcp.users(id) ON DELETE CASCADE,
    scope_type text NOT NULL CHECK (scope_type IN ('account', 'business', 'all')),
    scope_id text NOT NULL,
    access_level text NOT NULL CHECK (access_level IN ('read', 'write')),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, scope_type, scope_id)
);

CREATE TABLE IF NOT EXISTS meta_ads_mcp.audit_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id uuid REFERENCES meta_ads_mcp.users(id) ON DELETE SET NULL,
    key_id uuid REFERENCES meta_ads_mcp.api_keys(id) ON DELETE SET NULL,
    tool_name text NOT NULL,
    account_id text,
    outcome text NOT NULL CHECK (outcome IN ('success', 'denied', 'error')),
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS meta_audit_created_at_idx
    ON meta_ads_mcp.audit_log (created_at DESC);

ALTER TABLE meta_ads_mcp.access_grants
    DROP CONSTRAINT IF EXISTS access_grants_scope_type_check;
ALTER TABLE meta_ads_mcp.access_grants
    ADD CONSTRAINT access_grants_scope_type_check
    CHECK (scope_type IN ('account', 'business', 'all'));
"""


class PostgresRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def migrate(self) -> None:
        with psycopg.connect(self.database_url) as connection:
            connection.execute(SCHEMA_SQL)

    def save_oauth_state(self, state_hash: str, expires_at: datetime) -> None:
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                "INSERT INTO meta_ads_mcp.oauth_states (state_hash, expires_at) VALUES (%s, %s)",
                (state_hash, expires_at),
            )

    def consume_oauth_state(self, state_hash: str) -> bool:
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                """
                UPDATE meta_ads_mcp.oauth_states SET used_at=now()
                WHERE state_hash=%s AND used_at IS NULL AND expires_at > now()
                RETURNING state_hash
                """,
                (state_hash,),
            ).fetchone()
        return row is not None

    def upsert_oauth_connection(
        self,
        *,
        meta_user_id: str,
        meta_user_name: str,
        encrypted_access_token: str,
        granted_scopes: list[str],
        expires_at: datetime | None,
    ) -> None:
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """
                INSERT INTO meta_ads_mcp.oauth_connections
                    (meta_user_id, meta_user_name, encrypted_access_token, granted_scopes, expires_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (meta_user_id) DO UPDATE SET
                    meta_user_name=EXCLUDED.meta_user_name,
                    encrypted_access_token=EXCLUDED.encrypted_access_token,
                    granted_scopes=EXCLUDED.granted_scopes,
                    expires_at=EXCLUDED.expires_at,
                    active=true, updated_at=now()
                """,
                (meta_user_id, meta_user_name, encrypted_access_token, granted_scopes, expires_at),
            )

    def replace_inventory(self, businesses: list[dict[str, Any]], accounts: list[dict[str, Any]]) -> None:
        with psycopg.connect(self.database_url) as connection:
            connection_id = connection.execute(
                "SELECT id FROM meta_ads_mcp.oauth_connections WHERE active=true ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()[0]
            connection.execute("DELETE FROM meta_ads_mcp.businesses WHERE connection_id=%s", (connection_id,))
            connection.execute("DELETE FROM meta_ads_mcp.ad_accounts WHERE connection_id=%s", (connection_id,))
            for business in businesses:
                connection.execute(
                    "INSERT INTO meta_ads_mcp.businesses (business_id, connection_id, name) VALUES (%s,%s,%s)",
                    (str(business["id"]), connection_id, business.get("name") or str(business["id"])),
                )
            for account in accounts:
                account_id = _account_digits(str(account["id"]))
                connection.execute(
                    """INSERT INTO meta_ads_mcp.ad_accounts
                       (account_id, connection_id, name, currency, account_status)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (account_id, connection_id, account.get("name") or account_id, account.get("currency"), account.get("account_status")),
                )
                for business_id in account.get("business_ids", []):
                    connection.execute(
                        "INSERT INTO meta_ads_mcp.ad_account_businesses (account_id,business_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                        (account_id, str(business_id)),
                    )

    def get_encrypted_access_token(self) -> str:
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                """SELECT encrypted_access_token FROM meta_ads_mcp.oauth_connections
                   WHERE active=true AND (expires_at IS NULL OR expires_at > now())
                   ORDER BY updated_at DESC LIMIT 1"""
            ).fetchone()
        if row is None:
            raise RuntimeError("No active Meta OAuth connection. Open /oauth/meta/start first.")
        return row[0]

    async def find_api_key(self, token_prefix: str) -> ApiKeyIdentity | None:
        async with await psycopg.AsyncConnection.connect(self.database_url, row_factory=dict_row) as connection:
            row = await connection.execute(
                """SELECT k.id AS key_id, k.user_id, u.name AS user_name, k.token_hmac,
                          u.active AS user_active, k.active AS key_active, k.expires_at
                   FROM meta_ads_mcp.api_keys k JOIN meta_ads_mcp.users u ON u.id=k.user_id
                   WHERE k.token_prefix=%s""",
                (token_prefix,),
            )
            item = await row.fetchone()
        if item is None:
            return None
        return ApiKeyIdentity(**{key: str(value) if key in {"key_id", "user_id"} else value for key, value in item.items()})

    async def mark_api_key_used(self, key_id: str) -> None:
        async with await psycopg.AsyncConnection.connect(self.database_url) as connection:
            await connection.execute("UPDATE meta_ads_mcp.api_keys SET last_used_at=now() WHERE id=%s", (key_id,))

    def list_access_grants(self, user_id: str) -> list[AccessGrant]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT scope_type, scope_id, access_level FROM meta_ads_mcp.access_grants WHERE user_id=%s AND active=true",
                (user_id,),
            ).fetchall()
        return [AccessGrant(**row) for row in rows]

    def business_ids_for_account(self, account_id: str) -> list[str]:
        with psycopg.connect(self.database_url) as connection:
            rows = connection.execute(
                "SELECT business_id FROM meta_ads_mcp.ad_account_businesses WHERE account_id=%s",
                (_account_digits(account_id),),
            ).fetchall()
        return [row[0] for row in rows]

    def write_audit(self, *, user_id: str | None, key_id: str | None, tool_name: str, account_id: str | None, outcome: str, details: dict[str, Any] | None = None) -> None:
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """INSERT INTO meta_ads_mcp.audit_log
                   (user_id,key_id,tool_name,account_id,outcome,details)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb)""",
                (user_id, key_id, tool_name, _account_digits(account_id) if account_id else None, outcome, json.dumps(details or {})),
            )


def _account_digits(value: str) -> str:
    return value.lower().removeprefix("act_").replace("-", "")
