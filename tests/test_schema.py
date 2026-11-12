from meta_ads_mcp.database import SCHEMA_SQL


def test_schema_is_isolated_from_google_ads():
    assert "CREATE SCHEMA IF NOT EXISTS meta_ads_mcp" in SCHEMA_SQL
    assert "meta_ads_mcp.oauth_connections" in SCHEMA_SQL
    assert "meta_ads_mcp.oauth_states" in SCHEMA_SQL
    assert "meta_ads_mcp.users" in SCHEMA_SQL
    assert "meta_ads_mcp.api_keys" in SCHEMA_SQL
    assert "meta_ads_mcp.access_grants" in SCHEMA_SQL
    assert "meta_ads_mcp.audit_log" in SCHEMA_SQL
    assert "('account', 'business', 'all')" in SCHEMA_SQL
    assert "DROP CONSTRAINT IF EXISTS access_grants_scope_type_check" in SCHEMA_SQL
    assert "google_ads_mcp" not in SCHEMA_SQL
