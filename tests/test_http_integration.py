import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def test_http_client_can_discover_meta_ads_tools():
    port = _unused_port()
    fixture = Path(__file__).with_name("http_fixture_server.py")
    process = subprocess.Popen(
        [sys.executable, str(fixture), str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        _wait_until_listening(port)

        async def discover_tools():
            async with streamable_http_client(
                f"http://127.0.0.1:{port}/mcp"
            ) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    accounts = await session.call_tool("list_ad_accounts", {})
                    campaign = await session.call_tool(
                        "create_paused_campaign",
                        {
                            "account_id": "123",
                            "draft": {
                                "name": "Teste integrado",
                                "objective": "OUTCOME_TRAFFIC",
                            },
                        },
                    )
                    adsets = []
                    for promoted in (
                        {"page_id": "333", "whatsapp_phone_number": "+55 (61) 99999-9999"},
                        {"page_id": "333", "whats_app_business_phone_number_id": "444"},
                        {"page_id": "333", "whatsapp_phone_number_id": "444"},
                    ):
                        arguments = {
                            "account_id": "123",
                            "draft": {
                                "name": "WhatsApp integrado",
                                "campaign_id": "111",
                                "optimization_goal": "CONVERSATIONS",
                                "daily_budget_minor": 500,
                                "targeting": {"geo_locations": {"countries": ["BR"]}},
                                "destination_type": "WHATSAPP",
                                "promoted_object": promoted,
                            },
                        }
                        validation = await session.call_tool("validate_paused_adset", arguments)
                        adset = await session.call_tool("create_paused_adset", arguments)
                        adsets.append((validation, adset))
                    ads = []
                    for instagram_field in ("instagram_actor_id", "instagram_user_id"):
                        arguments = {
                            "account_id": "123",
                            "draft": {
                                "name": "Anuncio WhatsApp integrado",
                                "adset_id": "222",
                                "creative_spec": {
                                    "page_id": "333",
                                    instagram_field: "17841402054874577",
                                    "image_hash": "image-test",
                                    "link_url": "https://api.whatsapp.com/send?phone=556199636222",
                                    "message": "Consulte pneus pelo WhatsApp",
                                    "call_to_action_type": "WHATSAPP_MESSAGE",
                                },
                            },
                        }
                        validation = await session.call_tool("validate_paused_ad", arguments)
                        ad = await session.call_tool("create_paused_ad", arguments)
                        ads.append((validation, ad))
                    return tools, accounts, campaign, adsets, ads

        tools, accounts, campaign, adsets, ads = asyncio.run(discover_tools())
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    assert "campaign_insights" in {tool.name for tool in tools.tools}
    assert accounts.model_dump(by_alias=True)["structuredContent"] == {
        "result": [{"id": "act_123", "name": "Client"}]
    }
    assert campaign.model_dump(by_alias=True)["structuredContent"] == {
        "id": "campaign-test",
        "status": "PAUSED",
    }
    expected_promoted = [
        {"page_id": "333", "whatsapp_phone_number": "5561999999999"},
        {"page_id": "333", "whats_app_business_phone_number_id": "444"},
        {"page_id": "333", "whats_app_business_phone_number_id": "444"},
    ]
    for (validation, adset), expected in zip(adsets, expected_promoted, strict=True):
        assert not validation.is_error
        assert not adset.is_error
        request = validation.structured_content["request"]
        assert json.loads(request["promoted_object"]) == expected
        assert request["status"] == "PAUSED"
        assert adset.structured_content == {
            "id": "adset-test",
            "status": "PAUSED",
            "submitted_destination_type": "WHATSAPP",
            "submitted_promoted_object": expected,
        }
    creative_schema = next(tool for tool in tools.tools if tool.name == "create_paused_ad").input_schema["$defs"]["LinkCreativeSpec"]
    assert "instagram_user_id" in creative_schema["properties"]
    assert creative_schema["properties"]["instagram_actor_id"]["deprecated"] is True
    for validation, ad in ads:
        assert not validation.is_error
        assert not ad.is_error
        expected_story = {
            "page_id": "333",
            "instagram_user_id": "17841402054874577",
            "link_data": {
                "link": "https://api.whatsapp.com/send?phone=556199636222",
                "message": "Consulte pneus pelo WhatsApp",
                "image_hash": "image-test",
                "call_to_action": {"type": "WHATSAPP_MESSAGE"},
            },
        }
        request = validation.structured_content["request"]
        assert json.loads(request["creative"])["object_story_spec"] == expected_story
        assert request["status"] == "PAUSED"
        assert ad.structured_content == {
            "id": "ad-test",
            "status": "PAUSED",
            "submitted_creative": {"object_story_spec": expected_story},
        }


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until_listening(port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise AssertionError("HTTP MCP fixture did not start")
