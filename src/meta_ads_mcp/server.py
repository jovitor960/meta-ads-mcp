from __future__ import annotations

import base64
import hmac
import os
from typing import Any, Mapping, Protocol

from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .auth import AuthorizationService, PostgresTokenVerifier
from .campaigns import (
    AdImageUploadDraft,
    PausedAdDraft,
    PausedAdSetDraft,
    PausedAdSetUpdate,
    PausedAdUpdate,
    PausedCampaignDraft,
    PausedCampaignUpdate,
)
from .conversions import ConversionTestEventDraft, DatasetDraft
from .config import Settings
from .crypto import TokenCipher
from .database import PostgresRepository
from .gateway import MetaAdsGateway, MetaGraphApi, PostgresTokenSource
from .oauth import InvalidOAuthState, MetaGraphOAuthClient, MetaOAuthService


class ReportsGateway(Protocol):
    def list_ad_accounts(self) -> list[dict[str, Any]]: ...
    def get_ad_account(self, account_id: str) -> dict[str, Any]: ...
    def get_account_billing(self, account_id: str) -> dict[str, Any]: ...
    def list_campaigns(self, account_id: str, max_rows: int = 200) -> list[dict[str, Any]]: ...
    def get_insights(self, account_id: str, level: str, date_preset: str = "last_30d", max_rows: int = 500) -> list[dict[str, Any]]: ...
    def list_adsets(self, account_id: str, campaign_id: str | None = None, max_rows: int = 200) -> list[dict[str, Any]]: ...
    def list_ads(self, account_id: str, adset_id: str | None = None, max_rows: int = 200) -> list[dict[str, Any]]: ...
    def list_ad_creatives(self, account_id: str, max_rows: int = 200) -> list[dict[str, Any]]: ...
    def validate_paused_campaign_update(self, account_id: str, draft: PausedCampaignUpdate) -> dict[str, Any]: ...
    def update_paused_campaign(self, account_id: str, draft: PausedCampaignUpdate) -> dict[str, Any]: ...
    def upload_ad_image(self, account_id: str, draft: AdImageUploadDraft) -> dict[str, Any]: ...
    def list_conversion_datasets(self, account_id: str) -> list[dict[str, Any]]: ...
    def get_conversion_dataset(self, account_id: str, dataset_id: str) -> dict[str, Any]: ...


READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
VALIDATION = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
WRITE_PAUSED = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)


def create_server(
    gateway: ReportsGateway,
    authorization: AuthorizationService | None = None,
    token_verifier: PostgresTokenVerifier | None = None,
    auth_settings: AuthSettings | None = None,
    audit_repository: PostgresRepository | None = None,
    oauth_service: MetaOAuthService | None = None,
    oauth_admin_username: str = "admin",
    oauth_admin_password: str = "",
    enable_mutations: bool = False,
) -> MCPServer:
    server = MCPServer(
        name="meta-ads",
        instructions=(
            "Meta Ads reporting. "
            + (
                "Guarded write tools create campaigns, ad sets, and ads hardcoded as PAUSED; updates refuse resources that are not already PAUSED. Campaign updates and public image uploads are supported. There are no enable or delete tools."
                if enable_mutations
                else "Write tools are disabled in this deployment."
            )
        ),
        token_verifier=token_verifier,
        auth=auth_settings,
    )

    def authorize(account_id: str, tool_name: str, level: str = "read"):
        if authorization is None:
            return None
        try:
            return authorization.require(account_id, level)
        except Exception:
            _audit(audit_repository, authorization, tool_name, account_id, "denied")
            raise

    def success(actor, tool_name: str, account_id: str | None = None):
        if actor is not None and audit_repository is not None:
            audit_repository.write_audit(user_id=actor.user_id, key_id=actor.key_id, tool_name=tool_name, account_id=account_id, outcome="success")

    @server.tool(annotations=READ_ONLY)
    def list_ad_accounts() -> list[dict[str, Any]]:
        """List all ad accounts available through the connected Meta user."""
        rows = gateway.list_ad_accounts()
        if authorization is None:
            return rows
        actor = authorization.current_actor()
        filtered = authorization.filter_accounts(rows)
        success(actor, "list_ad_accounts")
        return filtered

    @server.tool(annotations=READ_ONLY)
    def get_ad_account(account_id: str) -> dict[str, Any]:
        """Return details for one Meta ad account."""
        actor = authorize(account_id, "get_ad_account")
        result = gateway.get_ad_account(account_id)
        success(actor, "get_ad_account", account_id)
        return result

    @server.tool(annotations=READ_ONLY)
    def get_account_billing(account_id: str) -> dict[str, Any]:
        """Read billing context, prepaid status and funding details. Available funds may be unavailable; balance is not proof of spendable credit. Monetary raw values retain Meta units."""
        actor = authorize(account_id, "get_account_billing")
        result = gateway.get_account_billing(account_id)
        success(actor, "get_account_billing", account_id)
        return result

    @server.tool(annotations=READ_ONLY)
    def list_campaigns(account_id: str, max_rows: int = 200) -> list[dict[str, Any]]:
        """List campaigns for one Meta ad account."""
        actor = authorize(account_id, "list_campaigns")
        result = gateway.list_campaigns(account_id, max_rows)
        success(actor, "list_campaigns", account_id)
        return result

    @server.tool(annotations=READ_ONLY)
    def list_adsets(account_id: str, campaign_id: str | None = None, max_rows: int = 200) -> list[dict[str, Any]]:
        """List ad sets for an account, optionally restricted to one campaign."""
        actor = authorize(account_id, "list_adsets")
        result = gateway.list_adsets(account_id, campaign_id, max_rows)
        success(actor, "list_adsets", account_id)
        return result

    @server.tool(annotations=READ_ONLY)
    def list_ads(account_id: str, adset_id: str | None = None, max_rows: int = 200) -> list[dict[str, Any]]:
        """List ads for an account, optionally restricted to one ad set."""
        actor = authorize(account_id, "list_ads")
        result = gateway.list_ads(account_id, adset_id, max_rows)
        success(actor, "list_ads", account_id)
        return result

    @server.tool(annotations=READ_ONLY)
    def list_ad_creatives(account_id: str, max_rows: int = 200) -> list[dict[str, Any]]:
        """List reusable ad creatives for an account."""
        actor = authorize(account_id, "list_ad_creatives")
        result = gateway.list_ad_creatives(account_id, max_rows)
        success(actor, "list_ad_creatives", account_id)
        return result

    @server.tool(annotations=READ_ONLY)
    def list_conversion_datasets(account_id: str) -> list[dict[str, Any]]:
        """List Meta Pixels/datasets linked to one authorized ad account."""
        actor = authorize(account_id, "list_conversion_datasets")
        result = gateway.list_conversion_datasets(account_id)
        success(actor, "list_conversion_datasets", account_id)
        return result

    @server.tool(annotations=READ_ONLY)
    def get_conversion_dataset(account_id: str, dataset_id: str) -> dict[str, Any]:
        """Inspect a dataset and prove it is linked to the authorized ad account."""
        actor = authorize(account_id, "get_conversion_dataset")
        result = gateway.get_conversion_dataset(account_id, dataset_id)
        success(actor, "get_conversion_dataset", account_id)
        return result

    def insights(account_id: str, level: str, date_preset: str, max_rows: int, tool_name: str):
        actor = authorize(account_id, tool_name)
        result = gateway.get_insights(account_id, level, date_preset=date_preset, max_rows=max_rows)
        success(actor, tool_name, account_id)
        return result

    @server.tool(annotations=READ_ONLY)
    def account_insights(account_id: str, date_preset: str = "last_30d", max_rows: int = 500) -> list[dict[str, Any]]:
        """Return account-level performance insights."""
        return insights(account_id, "account", date_preset, max_rows, "account_insights")

    @server.tool(annotations=READ_ONLY)
    def campaign_insights(account_id: str, date_preset: str = "last_30d", max_rows: int = 500) -> list[dict[str, Any]]:
        """Return campaign-level performance insights."""
        return insights(account_id, "campaign", date_preset, max_rows, "campaign_insights")

    @server.tool(annotations=READ_ONLY)
    def adset_insights(account_id: str, date_preset: str = "last_30d", max_rows: int = 500) -> list[dict[str, Any]]:
        """Return ad-set-level performance insights."""
        return insights(account_id, "adset", date_preset, max_rows, "adset_insights")

    @server.tool(annotations=READ_ONLY)
    def ad_insights(account_id: str, date_preset: str = "last_30d", max_rows: int = 500) -> list[dict[str, Any]]:
        """Return ad-level performance insights."""
        return insights(account_id, "ad", date_preset, max_rows, "ad_insights")

    if enable_mutations:
        def mutate(account_id: str, tool_name: str, callback):
            actor = authorize(account_id, tool_name, "write")
            try:
                result = callback()
                success(actor, tool_name, account_id)
                return result
            except Exception:
                _audit(audit_repository, authorization, tool_name, account_id, "error")
                raise

        @server.tool(annotations=VALIDATION)
        def validate_conversion_dataset(account_id: str, business_id: str, draft: DatasetDraft) -> dict[str, Any]:
            """Validate dataset creation parameters without writing to Meta."""
            return mutate(account_id, "validate_conversion_dataset", lambda: gateway.validate_conversion_dataset(account_id, business_id, draft))

        @server.tool(annotations=WRITE_PAUSED)
        def create_conversion_dataset(account_id: str, business_id: str, draft: DatasetDraft) -> dict[str, Any]:
            """Create a dataset in a business and link it to an authorized ad account."""
            return mutate(account_id, "create_conversion_dataset", lambda: gateway.create_conversion_dataset(account_id, business_id, draft))

        @server.tool(annotations=WRITE_PAUSED)
        def send_test_conversion_event(account_id: str, dataset_id: str, draft: ConversionTestEventDraft) -> dict[str, Any]:
            """Send one conversion forced to Meta Test Events by a required test_event_code."""
            return mutate(account_id, "send_test_conversion_event", lambda: gateway.send_test_conversion_event(account_id, dataset_id, draft))

        @server.tool(annotations=VALIDATION)
        def validate_paused_campaign(account_id: str, draft: PausedCampaignDraft) -> dict[str, Any]:
            """Validate a campaign that can only be created PAUSED."""
            return mutate(account_id, "validate_paused_campaign", lambda: gateway.validate_paused_campaign(account_id, draft))

        @server.tool(annotations=WRITE_PAUSED)
        def create_paused_campaign(account_id: str, draft: PausedCampaignDraft) -> dict[str, Any]:
            """Create one campaign hardcoded as PAUSED."""
            return mutate(account_id, "create_paused_campaign", lambda: gateway.create_paused_campaign(account_id, draft))

        @server.tool(annotations=VALIDATION)
        def validate_paused_campaign_update(account_id: str, draft: PausedCampaignUpdate) -> dict[str, Any]:
            """Validate changes to a campaign that must already be PAUSED."""
            return mutate(account_id, "validate_paused_campaign_update", lambda: gateway.validate_paused_campaign_update(account_id, draft))

        @server.tool(annotations=WRITE_PAUSED)
        def update_paused_campaign(account_id: str, draft: PausedCampaignUpdate) -> dict[str, Any]:
            """Update a campaign only when it is already PAUSED; keep it PAUSED."""
            return mutate(account_id, "update_paused_campaign", lambda: gateway.update_paused_campaign(account_id, draft))

        @server.tool(annotations=WRITE_PAUSED)
        def upload_ad_image(account_id: str, draft: AdImageUploadDraft) -> dict[str, Any]:
            """Upload a publicly reachable image URL to Meta and return its image hash."""
            return mutate(account_id, "upload_ad_image", lambda: gateway.upload_ad_image(account_id, draft))

        @server.tool(annotations=VALIDATION)
        def validate_paused_adset(account_id: str, draft: PausedAdSetDraft) -> dict[str, Any]:
            """Validate a PAUSED ad set under an ACTIVE or PAUSED campaign."""
            return mutate(account_id, "validate_paused_adset", lambda: gateway.validate_paused_adset(account_id, draft))

        @server.tool(annotations=WRITE_PAUSED)
        def create_paused_adset(account_id: str, draft: PausedAdSetDraft) -> dict[str, Any]:
            """Create one ad set hardcoded as PAUSED under an ACTIVE or PAUSED campaign."""
            return mutate(account_id, "create_paused_adset", lambda: gateway.create_paused_adset(account_id, draft))

        @server.tool(annotations=VALIDATION)
        def validate_paused_ad(account_id: str, draft: PausedAdDraft) -> dict[str, Any]:
            """Validate a PAUSED ad under ACTIVE or PAUSED campaign and ad set parents."""
            return mutate(account_id, "validate_paused_ad", lambda: gateway.validate_paused_ad(account_id, draft))

        @server.tool(annotations=WRITE_PAUSED)
        def create_paused_ad(account_id: str, draft: PausedAdDraft) -> dict[str, Any]:
            """Create one ad hardcoded as PAUSED under ACTIVE or PAUSED campaign and ad set parents."""
            return mutate(account_id, "create_paused_ad", lambda: gateway.create_paused_ad(account_id, draft))

        @server.tool(annotations=VALIDATION)
        def validate_paused_adset_update(account_id: str, draft: PausedAdSetUpdate) -> dict[str, Any]:
            """Validate changes to an ad set that must already be PAUSED."""
            return mutate(account_id, "validate_paused_adset_update", lambda: gateway.validate_paused_adset_update(account_id, draft))

        @server.tool(annotations=WRITE_PAUSED)
        def update_paused_adset(account_id: str, draft: PausedAdSetUpdate) -> dict[str, Any]:
            """Update an ad set only when it is already PAUSED; keep it PAUSED."""
            return mutate(account_id, "update_paused_adset", lambda: gateway.update_paused_adset(account_id, draft))

        @server.tool(annotations=VALIDATION)
        def validate_paused_ad_update(account_id: str, draft: PausedAdUpdate) -> dict[str, Any]:
            """Validate changes to an ad that must already be PAUSED."""
            return mutate(account_id, "validate_paused_ad_update", lambda: gateway.validate_paused_ad_update(account_id, draft))

        @server.tool(annotations=WRITE_PAUSED)
        def update_paused_ad(account_id: str, draft: PausedAdUpdate) -> dict[str, Any]:
            """Update an ad only when it is already PAUSED; keep it PAUSED."""
            return mutate(account_id, "update_paused_ad", lambda: gateway.update_paused_ad(account_id, draft))

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok", "service": "meta-ads-mcp", "version": "0.2.1"})

    if oauth_service is not None:
        @server.custom_route("/oauth/meta/start", methods=["GET"])
        async def oauth_start(request: Request) -> Response:
            if not _valid_basic_auth(request, oauth_admin_username, oauth_admin_password):
                return Response("Authentication required", status_code=401, headers={"WWW-Authenticate": 'Basic realm="Meta OAuth"'})
            return RedirectResponse(oauth_service.authorization_url(), status_code=302)

        @server.custom_route("/oauth/meta/callback", methods=["GET"])
        async def oauth_callback(request: Request) -> Response:
            error = request.query_params.get("error")
            if error:
                return HTMLResponse(f"<h1>Conexão cancelada</h1><p>{error}</p>", status_code=400)
            code = request.query_params.get("code")
            state = request.query_params.get("state")
            if not code or not state:
                return HTMLResponse("<h1>Callback inválido</h1>", status_code=400)
            try:
                result = oauth_service.complete(code=code, state=state)
            except InvalidOAuthState as exc:
                return HTMLResponse(f"<h1>Estado OAuth inválido</h1><p>{exc}</p>", status_code=400)
            except Exception as exc:
                return HTMLResponse(f"<h1>Falha na conexão Meta</h1><p>{type(exc).__name__}</p>", status_code=502)
            return HTMLResponse(f"<h1>Meta Ads conectado</h1><p>{result['name']}: {result['businesses']} negócios e {result['ad_accounts']} contas sincronizadas.</p>")

    return server


def run_server(server: Any, env: Mapping[str, str]) -> None:
    transport = env.get("MCP_TRANSPORT", "stdio").strip().lower()
    if transport == "stdio":
        server.run(transport="stdio")
        return
    if transport == "streamable-http":
        server.run(transport="streamable-http", host=env.get("MCP_HOST", "0.0.0.0"), port=int(env.get("MCP_PORT", "8000")), stateless_http=True, json_response=True)
        return
    raise ValueError(f"Unsupported MCP transport: {transport}")


def main() -> None:
    settings = Settings.from_env(os.environ)
    repository = PostgresRepository(settings.database_url)
    repository.migrate()
    cipher = TokenCipher(settings.encryption_key)
    token_source = PostgresTokenSource(repository, cipher)
    gateway = MetaAdsGateway(
        MetaGraphApi(token_source=token_source, app_secret=settings.app_secret, graph_version=settings.graph_version),
        max_budget_minor=settings.max_budget_minor,
    )
    oauth_client = MetaGraphOAuthClient(settings.app_id, settings.app_secret, settings.graph_version)
    oauth_service = MetaOAuthService(app_id=settings.app_id, redirect_uri=settings.redirect_uri, scopes=settings.oauth_scopes, repository=repository, client=oauth_client, cipher=cipher)
    authorization = verifier = auth_settings = None
    if settings.auth_required:
        authorization = AuthorizationService(repository)
        verifier = PostgresTokenVerifier(repository, settings.token_pepper)
        auth_settings = AuthSettings(issuer_url=settings.public_url, resource_server_url=settings.public_url, required_scopes=["mcp"])
    server = create_server(
        gateway,
        authorization,
        verifier,
        auth_settings,
        repository,
        oauth_service,
        settings.oauth_admin_username,
        settings.oauth_admin_password,
        enable_mutations=settings.enable_mutations,
    )
    run_server(server, os.environ)


def _valid_basic_auth(request: Request, expected_user: str, expected_password: str) -> bool:
    header = request.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
        username, password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return False
    return hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_password)


def _audit(repository, authorization, tool_name: str, account_id: str | None, outcome: str) -> None:
    if repository is None:
        return
    try:
        actor = authorization.current_actor()
    except Exception:
        return
    repository.write_audit(user_id=actor.user_id, key_id=actor.key_id, tool_name=tool_name, account_id=account_id, outcome=outcome)


if __name__ == "__main__":
    main()
