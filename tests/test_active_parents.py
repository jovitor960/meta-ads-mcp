import pytest
from meta_ads_mcp.campaigns import PausedAdDraft, PausedAdSetDraft
from meta_ads_mcp.gateway import MetaAdsGateway, MutationError
from test_mutations import FakeMutationApi

def api(campaign_status='ACTIVE',adset_status='ACTIVE'):
    a=FakeMutationApi()
    a.objects={'111':dict(id='111',account_id='123',status=campaign_status,objective='OUTCOME_TRAFFIC'),'222':dict(id='222',account_id='123',campaign_id='111',status=adset_status)}
    return a

@pytest.mark.parametrize('campaign_status',['ACTIVE','PAUSED'])
def test_new_adsets_are_paused_under_active_or_paused_campaign(campaign_status):
    a=api(campaign_status); g=MetaAdsGateway(a)
    draft=PausedAdSetDraft(name='New paused set',campaign_id='111',optimization_goal='LINK_CLICKS',daily_budget_minor=1000,targeting={'geo_locations':{'countries':['BR']}})
    assert g.validate_paused_adset('123',draft)['forced_status']=='PAUSED'
    assert not a.posts
    assert g.create_paused_adset('123',draft)['status']=='PAUSED'
    assert a.posts[-1][1]['status']=='PAUSED'
@pytest.mark.parametrize('campaign_status',['ACTIVE','PAUSED'])
@pytest.mark.parametrize('adset_status',['ACTIVE','PAUSED'])
def test_new_ads_are_paused_under_active_or_paused_parents(campaign_status,adset_status):
    a=api(campaign_status,adset_status); g=MetaAdsGateway(a)
    draft=PausedAdDraft(name='New paused ad',adset_id='222',creative_id='333')
    assert g.validate_paused_ad('123',draft)['forced_status']=='PAUSED'
    assert not a.posts
    assert g.create_paused_ad('123',draft)['status']=='PAUSED'
    assert a.posts[-1][1]['status']=='PAUSED'
@pytest.mark.parametrize('parent',['111','222'])
@pytest.mark.parametrize('field,value',[('status','DELETED'),('status','ARCHIVED'),('status',None),('account_id','999')])
def test_creation_and_validation_reject_invalid_parent_or_foreign_account(parent,field,value):
    a=api(); a.objects[parent][field]=value; g=MetaAdsGateway(a)
    draft=PausedAdDraft(name='Blocked',adset_id='222',creative_id='333')
    for call in (g.validate_paused_ad,g.create_paused_ad):
        with pytest.raises(MutationError): call('123',draft)
    assert not a.posts
def test_http_mcp_creates_only_paused_children_under_active_parents():
    import asyncio,json,os,subprocess,sys
    from pathlib import Path
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from test_http_integration import _unused_port,_wait_until_listening
    port=_unused_port()
    process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('http_fixture_server.py')),str(port)],env={**os.environ,'META_FIXTURE_PARENT_STATUS':'ACTIVE'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    async def exercise():
        async with streamable_http_client(f'http://127.0.0.1:{port}/mcp') as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                for kind,draft in [('adset',dict(name='Paused child',campaign_id='111',optimization_goal='CONVERSATIONS',destination_type='WHATSAPP',promoted_object={'page_id':'333','whatsapp_phone_number':'+5561999999999'},daily_budget_minor=1000,targeting={'geo_locations':{'countries':['BR']}})),('ad',dict(name='Paused child',adset_id='222',creative_id='333'))]:
                    for prefix in ('validate','create'):
                        r=(await session.call_tool(f'{prefix}_paused_{kind}',dict(account_id='123',draft=draft))).model_dump(by_alias=True)
                        assert not r.get('isError'),r
                        data=r.get('structuredContent') or json.loads(r['content'][0]['text'])
                        assert data.get('forced_status',data.get('status'))=='PAUSED'
    try:
        _wait_until_listening(port); asyncio.run(exercise())
    finally:
        process.terminate(); process.wait(timeout=10)

