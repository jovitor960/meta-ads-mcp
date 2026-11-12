import asyncio

from meta_ads_mcp.server import create_server, run_server


class FakeGateway:
    pass


def test_mcp_exposes_only_read_only_tools():
    server = create_server(FakeGateway())

    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} == {
        "list_ad_accounts",
        "get_ad_account",
        "get_account_billing",
        "list_campaigns",
        "list_adsets",
        "list_ads",
        "list_ad_creatives",
        "account_insights",
        "campaign_insights",
        "adset_insights",
        "ad_insights",
        "list_conversion_datasets",
        "get_conversion_dataset",
    }
    annotations = [tool.annotations.model_dump(by_alias=True) for tool in tools]
    assert all(item["readOnlyHint"] is True for item in annotations)
    assert all(item["destructiveHint"] is False for item in annotations)


def test_mcp_exposes_guarded_paused_mutations_only_when_enabled():
    server = create_server(FakeGateway(), enable_mutations=True)

    tools = asyncio.run(server.list_tools())
    by_name = {tool.name: tool for tool in tools}

    expected = {
        "list_conversion_datasets",
        "get_conversion_dataset",
        "validate_conversion_dataset",
        "create_conversion_dataset",
        "send_test_conversion_event",
        "validate_paused_campaign",
        "create_paused_campaign",
        "validate_paused_campaign_update",
        "update_paused_campaign",
        "upload_ad_image",
        "validate_paused_adset",
        "create_paused_adset",
        "validate_paused_ad",
        "create_paused_ad",
        "validate_paused_adset_update",
        "update_paused_adset",
        "validate_paused_ad_update",
        "update_paused_ad",
    }
    assert expected <= set(by_name)
    assert by_name["list_conversion_datasets"].annotations.read_only_hint is True
    assert by_name["get_conversion_dataset"].annotations.read_only_hint is True
    assert by_name["validate_conversion_dataset"].annotations.read_only_hint is True
    assert by_name["create_conversion_dataset"].annotations.read_only_hint is False
    assert by_name["send_test_conversion_event"].annotations.read_only_hint is False


class FakeServer:
    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)


def test_server_uses_streamable_http_configuration_for_container_runtime():
    server = FakeServer()

    run_server(
        server,
        {
            "MCP_TRANSPORT": "streamable-http",
            "MCP_HOST": "0.0.0.0",
            "MCP_PORT": "8124",
        },
    )

    assert server.calls == [
        {
            "transport": "streamable-http",
            "host": "0.0.0.0",
            "port": 8124,
            "stateless_http": True,
            "json_response": True,
        }
    ]
