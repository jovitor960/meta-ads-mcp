from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ConfigError(ValueError):
    """Raised when required deployment configuration is invalid."""


DEFAULT_SCOPES = (
    "ads_read",
    "ads_management",
    "business_management",
)


@dataclass(frozen=True)
class Settings:
    app_id: str
    app_secret: str
    public_url: str
    database_url: str
    encryption_key: str
    token_pepper: str
    oauth_admin_password: str
    enable_mutations: bool = False
    max_budget_minor: int | None = None
    oauth_admin_username: str = "admin"
    graph_version: str = "v26.0"
    oauth_scopes: tuple[str, ...] = DEFAULT_SCOPES
    auth_required: bool = True

    @property
    def redirect_uri(self) -> str:
        return f"{self.public_url}/oauth/meta/callback"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        public_url = _required(env, "MCP_PUBLIC_URL").rstrip("/")
        if not public_url.lower().startswith("https://"):
            raise ConfigError("MCP_PUBLIC_URL must use HTTPS.")
        pepper = _required_secret(env, "MCP_TOKEN_PEPPER")
        if len(pepper) < 32:
            raise ConfigError("MCP_TOKEN_PEPPER must contain at least 32 characters.")
        scopes = tuple(
            item.strip()
            for item in env.get("META_OAUTH_SCOPES", ",".join(DEFAULT_SCOPES)).split(",")
            if item.strip()
        )
        enable_mutations = _boolean(env, "META_ADS_ENABLE_MUTATIONS", False)
        max_budget_minor = _optional_positive_integer(env, "META_ADS_MAX_BUDGET_MINOR")
        if enable_mutations and max_budget_minor is None:
            raise ConfigError(
                "META_ADS_MAX_BUDGET_MINOR is required when mutations are enabled."
            )
        return cls(
            app_id=_required(env, "META_APP_ID"),
            app_secret=_required_secret(env, "META_APP_SECRET"),
            public_url=public_url,
            database_url=_required_secret(env, "MCP_DATABASE_URL"),
            encryption_key=_required_secret(env, "META_TOKEN_ENCRYPTION_KEY"),
            token_pepper=pepper,
            oauth_admin_password=_required_secret(env, "META_OAUTH_ADMIN_PASSWORD"),
            enable_mutations=enable_mutations,
            max_budget_minor=max_budget_minor,
            oauth_admin_username=env.get("META_OAUTH_ADMIN_USERNAME", "admin").strip() or "admin",
            graph_version=env.get("META_GRAPH_API_VERSION", "v26.0").strip() or "v26.0",
            oauth_scopes=scopes,
            auth_required=_boolean(env, "MCP_AUTH_REQUIRED", True),
        )


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _required_secret(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if value:
        return value
    file_name = env.get(f"{name}_FILE", "").strip()
    if file_name:
        path = Path(file_name.strip("'\""))
        if not path.is_file():
            raise ConfigError(f"{name}_FILE does not point to an existing file.")
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    raise ConfigError(f"Missing required environment variable: {name} or {name}_FILE")


def _boolean(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"true", "1", "yes"}:
        return True
    if raw in {"false", "0", "no"}:
        return False
    raise ConfigError(f"{name} must be true or false.")


def _optional_positive_integer(env: Mapping[str, str], name: str) -> int | None:
    raw = env.get(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a positive integer.") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be a positive integer.")
    return value
