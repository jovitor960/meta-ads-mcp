import pytest

from meta_ads_mcp.config import ConfigError, Settings


def _secret(tmp_path, name, value):
    path = tmp_path / name
    path.write_text(value + "\n", encoding="utf-8")
    return str(path)


def test_configuration_reads_oauth_and_database_secrets(tmp_path):
    settings = Settings.from_env(
        {
            "META_APP_ID": "428004393101538",
            "META_APP_SECRET_FILE": _secret(tmp_path, "app", "app-secret"),
            "MCP_DATABASE_URL_FILE": _secret(
                tmp_path, "db", "postgresql://meta:pass@postgres:5432/ads"
            ),
            "META_TOKEN_ENCRYPTION_KEY_FILE": _secret(
                tmp_path, "encryption", "a" * 44
            ),
            "MCP_TOKEN_PEPPER_FILE": _secret(tmp_path, "pepper", "p" * 64),
            "META_OAUTH_ADMIN_PASSWORD_FILE": _secret(
                tmp_path, "admin", "admin-password"
            ),
            "MCP_PUBLIC_URL": "https://meta-ads-mcp.autoengrena.com.br/",
            "META_ADS_ENABLE_MUTATIONS": "true",
            "META_ADS_MAX_BUDGET_MINOR": "500000",
        }
    )

    assert settings.app_id == "428004393101538"
    assert settings.public_url == "https://meta-ads-mcp.autoengrena.com.br"
    assert settings.redirect_uri.endswith("/oauth/meta/callback")
    assert settings.graph_version == "v26.0"
    assert "ads_read" in settings.oauth_scopes
    assert "ads_management" in settings.oauth_scopes
    assert settings.enable_mutations is True
    assert settings.max_budget_minor == 500_000


def test_configuration_rejects_non_https_public_url(tmp_path):
    env = {
        "META_APP_ID": "123",
        "META_APP_SECRET": "secret",
        "MCP_DATABASE_URL": "postgresql://meta:pass@postgres/ads",
        "META_TOKEN_ENCRYPTION_KEY": "a" * 44,
        "MCP_TOKEN_PEPPER": "p" * 64,
        "META_OAUTH_ADMIN_PASSWORD": "password",
        "MCP_PUBLIC_URL": "http://example.test",
    }
    with pytest.raises(ConfigError, match="HTTPS"):
        Settings.from_env(env)


def test_configuration_requires_budget_cap_when_mutations_are_enabled(tmp_path):
    env = {
        "META_APP_ID": "123",
        "META_APP_SECRET": "secret",
        "MCP_DATABASE_URL": "postgresql://meta:pass@postgres/ads",
        "META_TOKEN_ENCRYPTION_KEY": "a" * 44,
        "MCP_TOKEN_PEPPER": "p" * 64,
        "META_OAUTH_ADMIN_PASSWORD": "password",
        "MCP_PUBLIC_URL": "https://example.test",
        "META_ADS_ENABLE_MUTATIONS": "true",
    }
    with pytest.raises(ConfigError, match="META_ADS_MAX_BUDGET_MINOR"):
        Settings.from_env(env)
