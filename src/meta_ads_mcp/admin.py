from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from .config import _required_secret


def generate_token(pepper: str):
    prefix = secrets.token_urlsafe(9)
    token = f"mcp_{prefix}.{secrets.token_urlsafe(32)}"
    digest = hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest()
    return prefix, token, digest


def main() -> None:
    args = _parser().parse_args()
    with psycopg.connect(_required_secret(os.environ, "MCP_DATABASE_URL"), row_factory=dict_row) as connection:
        _execute(connection, args, _required_secret(os.environ, "MCP_TOKEN_PEPPER"))


def _execute(connection, args, pepper: str):
    if args.command == "create-user":
        row = connection.execute(
            """INSERT INTO meta_ads_mcp.users (name,email) VALUES (%s,lower(%s))
               ON CONFLICT ((lower(email))) DO UPDATE SET name=EXCLUDED.name,active=true,updated_at=now()
               RETURNING id,name,email,active""", (args.name, args.email)).fetchone()
        print(dict(row))
    elif args.command == "disable-user":
        connection.execute("UPDATE meta_ads_mcp.users SET active=false,updated_at=now() WHERE lower(email)=lower(%s)", (args.email,))
        print("Usuário desativado; os tokens param de funcionar imediatamente.")
    elif args.command == "issue-token":
        user = _user(connection, args.email)
        prefix, token, digest = generate_token(pepper)
        expires_at = datetime.now(UTC) + timedelta(days=args.expires_days) if args.expires_days else None
        connection.execute("INSERT INTO meta_ads_mcp.api_keys (user_id,device_name,token_prefix,token_hmac,expires_at) VALUES (%s,%s,%s,%s,%s)", (user["id"], args.device, prefix, digest, expires_at))
        print(f"TOKEN (exibido uma única vez): {token}")
        print(f"PREFIXO para revogação: {prefix}")
    elif args.command == "revoke-token":
        connection.execute("UPDATE meta_ads_mcp.api_keys SET active=false,revoked_at=now() WHERE token_prefix=%s", (args.prefix,))
        print("Token revogado.")
    elif args.command == "grant":
        user = _user(connection, args.email)
        connection.execute(
            """INSERT INTO meta_ads_mcp.access_grants (user_id,scope_type,scope_id,access_level)
               VALUES (%s,%s,%s,%s) ON CONFLICT (user_id,scope_type,scope_id)
               DO UPDATE SET access_level=EXCLUDED.access_level,active=true,updated_at=now()""",
            (user["id"], args.scope, _scope_id(args.scope, args.scope_id), args.access))
        print("Permissão aplicada.")
    elif args.command == "revoke-grant":
        user = _user(connection, args.email)
        connection.execute("UPDATE meta_ads_mcp.access_grants SET active=false,updated_at=now() WHERE user_id=%s AND scope_type=%s AND scope_id=%s", (user["id"], args.scope, _scope_id(args.scope, args.scope_id)))
        print("Permissão revogada.")
    elif args.command == "list-users":
        for row in connection.execute("SELECT id,name,email,active,created_at FROM meta_ads_mcp.users ORDER BY name").fetchall(): print(dict(row))
    elif args.command == "list-grants":
        for row in connection.execute("SELECT u.email,g.scope_type,g.scope_id,g.access_level,g.active FROM meta_ads_mcp.access_grants g JOIN meta_ads_mcp.users u ON u.id=g.user_id ORDER BY u.email,g.scope_type,g.scope_id").fetchall(): print(dict(row))
    elif args.command == "list-businesses":
        for row in connection.execute("SELECT business_id,name,updated_at FROM meta_ads_mcp.businesses ORDER BY name").fetchall(): print(dict(row))
    elif args.command == "list-accounts":
        for row in connection.execute(
            """SELECT a.account_id,a.name,a.currency,a.account_status,
                      COALESCE(array_agg(ab.business_id) FILTER (WHERE ab.business_id IS NOT NULL),'{}') AS business_ids
               FROM meta_ads_mcp.ad_accounts a
               LEFT JOIN meta_ads_mcp.ad_account_businesses ab ON ab.account_id=a.account_id
               GROUP BY a.account_id,a.name,a.currency,a.account_status ORDER BY a.name"""
        ).fetchall(): print(dict(row))


def _user(connection, email):
    row = connection.execute("SELECT id,active FROM meta_ads_mcp.users WHERE lower(email)=lower(%s)", (email,)).fetchone()
    if row is None: raise SystemExit("Usuário não encontrado. Execute create-user primeiro.")
    if not row["active"]: raise SystemExit("Usuário está desativado.")
    return row


def _digits(value):
    normalized = value.lower().removeprefix("act_").replace("-", "")
    if not normalized.isdigit(): raise SystemExit("scope-id deve conter apenas dígitos, hífens ou prefixo act_.")
    return normalized


def _scope_id(scope, value):
    if scope == "all":
        if value not in (None, "*"):
            raise SystemExit("scope all não aceita um scope-id diferente de *.")
        return "*"
    if not value:
        raise SystemExit("--scope-id é obrigatório para account e business.")
    return _digits(value)


def _parser():
    parser = argparse.ArgumentParser(prog="meta-ads-mcp-admin")
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("create-user"); command.add_argument("--name", required=True); command.add_argument("--email", required=True)
    command = sub.add_parser("disable-user"); command.add_argument("--email", required=True)
    command = sub.add_parser("issue-token"); command.add_argument("--email", required=True); command.add_argument("--device", required=True); command.add_argument("--expires-days", type=int)
    command = sub.add_parser("revoke-token"); command.add_argument("--prefix", required=True)
    for name in ("grant", "revoke-grant"):
        command = sub.add_parser(name); command.add_argument("--email", required=True); command.add_argument("--scope", choices=("account", "business", "all"), required=True); command.add_argument("--scope-id")
        if name == "grant": command.add_argument("--access", choices=("read", "write"), required=True)
    sub.add_parser("list-users"); sub.add_parser("list-grants")
    sub.add_parser("list-businesses"); sub.add_parser("list-accounts")
    return parser


if __name__ == "__main__": main()
