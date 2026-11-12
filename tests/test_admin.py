from meta_ads_mcp.admin import _parser


def test_admin_cli_exposes_inventory_commands():
    parser = _parser()
    assert parser.parse_args(["list-businesses"]).command == "list-businesses"
    assert parser.parse_args(["list-accounts"]).command == "list-accounts"


def test_admin_cli_accepts_all_scope_without_scope_id():
    args = _parser().parse_args(
        [
            "grant",
            "--email",
            "reader@example.com",
            "--scope",
            "all",
            "--access",
            "read",
        ]
    )

    assert args.scope == "all"
    assert args.scope_id is None
