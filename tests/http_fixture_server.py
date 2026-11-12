import json
import os
import sys

from meta_ads_mcp.gateway import MetaAdsGateway
from meta_ads_mcp.server import create_server, run_server


class FixtureMetaApi:
    def list_ad_accounts(self):
        return [{"id": "act_123", "name": "Client"}]

    def get_ad_account(self, account_id):
        return {"id": account_id}

    def list_campaigns(self, account_id, max_rows=200):
        return []

    def get_insights(
        self,
        account_id,
        level,
        date_preset="last_30d",
        max_rows=500,
    ):
        return []

    def get_object(self, object_id, fields):
        return {"id": object_id, "account_id": "123", "campaign_id": "111", "status": os.environ.get("META_FIXTURE_PARENT_STATUS", "PAUSED"), "objective": "OUTCOME_LEADS"}

    def post(self, path, params):
        if "creative" in params:
            return {"id": "ad-test", "submitted_creative": json.loads(params["creative"])}
        if path.endswith("/adsets"):
            return {
                "id": "adset-test",
                "submitted_promoted_object": json.loads(params["promoted_object"]),
                "submitted_destination_type": params["destination_type"],
            }
        return {"id": "campaign-test"}


if __name__ == "__main__":
    run_server(
        create_server(MetaAdsGateway(FixtureMetaApi()), enable_mutations=True),
        {
            "MCP_TRANSPORT": "streamable-http",
            "MCP_HOST": "127.0.0.1",
            "MCP_PORT": sys.argv[1],
        },
    )
