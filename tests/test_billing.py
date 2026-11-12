import asyncio
from meta_ads_mcp.gateway import MetaAdsGateway
from meta_ads_mcp.server import create_server

class BillingApi:
    def get_ad_account(self, account_id):
        return {'id': account_id, 'currency': 'BRL', 'balance': '500'}
    def get_object(self, object_id, fields):
        return {fields: {'is_prepay_account': True, 'funding_source_details': {'display_string': 'Funds available: R$ 123,45'}, 'spend_cap': '10000', 'user_tasks': ['MANAGE']}[fields]}

def test_billing_exposes_financial_context_without_inventing_available_funds():
    result = MetaAdsGateway(BillingApi()).get_account_billing('123')
    assert result['account_id'] == 'act_123'
    assert result['is_prepay_account'] is True
    assert result['balance_raw'] == '500'
    assert result['funding_source_details']['display_string'] == 'Funds available: R$ 123,45'
    assert result['available_funds'] is None
    assert result['available_funds_status'] == 'unavailable'
    assert result['queried_at']
    tools = asyncio.run(create_server(MetaAdsGateway(BillingApi())).list_tools())
    assert next(t for t in tools if t.name == 'get_account_billing').annotations.read_only_hint
from meta_ads_mcp.gateway import QueryError

def test_optional_financial_failure_preserves_account_and_never_becomes_zero():
    class RestrictedApi(BillingApi):
        def get_object(self, object_id, fields):
            if fields == 'funding_source_details':
                raise QueryError('permission denied')
            return {} if fields == 'spend_cap' else super().get_object(object_id, fields)
    result = MetaAdsGateway(RestrictedApi()).get_account_billing('123')
    assert result['balance_raw'] == '500'
    assert result['funding_source_details'] is None
    assert result['spend_cap'] is None
    assert result['field_errors']['funding_source_details'].startswith('query_failed')
    assert result['field_errors']['spend_cap'] == 'not_returned'
    assert result['available_funds'] is None
def test_available_brl_funds_are_read_from_explicit_meta_label():
    class PrepayApi(BillingApi):
        def get_object(self, object_id, fields):
            if fields == 'funding_source_details':
                return {fields: {'display_string': 'Saldo disponível (R$1.234,56\u00a0BRL)', 'type': 20}}
            return super().get_object(object_id, fields)
    result = MetaAdsGateway(PrepayApi()).get_account_billing('123')
    assert result['available_funds'] == '1234.56'
    assert result['available_funds_status'] == 'reported_by_meta'
    assert result['available_funds_source'] == 'funding_source_details.display_string'
import pytest

@pytest.mark.parametrize('display,prepay,currency', [
    ('VISA *1234', True, 'BRL'),
    ('Saldo dispon\u00edvel (R$123,45 BRL)', False, 'BRL'),
    ('Saldo dispon\u00edvel (R$123,45 BRL)', True, 'USD'),
    ('Saldo dispon\u00edvel (R$12.34,56 BRL)', True, 'BRL'),
    ('Saldo dispon\u00edvel (R$-10,00 BRL)', True, 'BRL'),
])
def test_unrecognized_or_inapplicable_funds_are_never_inferred(display, prepay, currency):
    class Api(BillingApi):
        def get_ad_account(self, account_id):
            return dict(super().get_ad_account(account_id), currency=currency)
        def get_object(self, object_id, fields):
            return {fields: {'display_string': display}} if fields == 'funding_source_details' else ({fields: prepay} if fields == 'is_prepay_account' else super().get_object(object_id, fields))
    assert MetaAdsGateway(Api()).get_account_billing('123')['available_funds'] is None

def test_billing_requires_account_read_permission_before_querying():
    class Denied:
        def require(self, account_id, level):
            assert account_id == 'act_123'
            assert level == 'read'
            raise PermissionError('denied')
    class NeverQuery:
        def get_account_billing(self, account_id):
            raise AssertionError('must not query an unauthorized account')
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError, match='get_account_billing'):
        asyncio.run(create_server(NeverQuery(), authorization=Denied()).call_tool('get_account_billing', {'account_id': 'act_123'}))

