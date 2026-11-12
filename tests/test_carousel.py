import json
import pytest
from pydantic import ValidationError
from meta_ads_mcp.campaigns import PausedAdDraft, PausedAdUpdate
from meta_ads_mcp.gateway import MetaAdsGateway
from test_mutations import FakeMutationApi

def spec():
    return dict(page_id='333', instagram_user_id='444', link_url='https://example.com/', message='Carousel', cards=[dict(link_url='https://example.com/one',image_hash='hash1',headline='First'),dict(link_url='https://example.com/two',image_url='https://example.com/two.jpg',description='Second')])

def api():
    a=FakeMutationApi()
    a.objects={'111':dict(id='111',account_id='123',status='PAUSED'), '222':dict(id='222',account_id='123',campaign_id='111',status='PAUSED'),'555':dict(id='555',account_id='123',status='PAUSED')}
    return a

def test_carousel_create_serializes_ordered_cards_and_stays_paused():
    a=api()
    MetaAdsGateway(a).create_paused_ad('123',PausedAdDraft(name='Carousel',adset_id='222',creative_spec=spec()))
    payload=a.posts[-1][1]
    assert payload['status']=='PAUSED'
    story=json.loads(payload['creative'])['object_story_spec']
    assert story['instagram_user_id']=='444'
    cards=story['link_data']['child_attachments']
    assert cards[0]['image_hash']=='hash1'
    assert cards[0]['name']=='First'
    assert cards[1]['picture']=='https://example.com/two.jpg'
    assert cards[1]['link']=='https://example.com/two'
    assert cards[1]['call_to_action']['type']=='LEARN_MORE'
    assert 'picture' not in story['link_data']
@pytest.mark.parametrize('change',[
    {'cards':[]}, {'cards':[spec()['cards'][0]]}, {'cards':[spec()['cards'][0]]*11},
    {'image_hash':'mixed'},
    {'cards':[dict(link_url='https://example.com'),spec()['cards'][1]]},
    {'cards':[dict(link_url='https://example.com',image_hash='a',image_url='https://example.com/a.jpg'),spec()['cards'][1]]},
    {'cards':[dict(link_url='javascript:bad',image_hash='a'),spec()['cards'][1]]},
])
def test_invalid_carousels_are_rejected_before_api_call(change):
    with pytest.raises(ValidationError):
        PausedAdDraft(name='Invalid',adset_id='222',creative_spec={**spec(),**change})

def test_update_and_validation_support_carousel_without_enabling_ad():
    a=api(); g=MetaAdsGateway(a)
    draft=PausedAdUpdate(ad_id='555',creative_spec=spec())
    validation=g.validate_paused_ad_update('123',draft)
    assert not a.posts
    g.update_paused_ad('123',draft)
    params=a.posts[-1][1]
    assert params['status']=='PAUSED'
    assert len(json.loads(params['creative'])['object_story_spec']['link_data']['child_attachments'])==2

@pytest.mark.parametrize('status,account',[('ACTIVE','123'),('PAUSED','999')])
def test_carousel_preserves_existing_status_and_account_guards(status,account):
    from meta_ads_mcp.gateway import MutationError
    a=api(); a.objects['555'].update(status=status,account_id=account)
    with pytest.raises(MutationError):
        MetaAdsGateway(a).update_paused_ad('123',PausedAdUpdate(ad_id='555',creative_spec=spec()))
    assert not a.posts
def test_carousel_is_available_and_serialized_through_http_mcp():
    import asyncio, subprocess, sys
    from pathlib import Path
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from test_http_integration import _unused_port, _wait_until_listening
    port=_unused_port()
    process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('http_fixture_server.py')),str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    async def exercise():
        async with streamable_http_client(f'http://127.0.0.1:{port}/mcp') as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                result=await session.call_tool('validate_paused_ad',dict(account_id='123',draft=dict(name='Carousel HTTP',adset_id='222',creative_spec=spec())))
                data=result.model_dump(by_alias=True)
                assert not data.get('isError')
                payload=data.get('structuredContent') or json.loads(data['content'][0]['text'])
                # The validation reports the exact request that creation submits.
                assert 'child_attachments' in json.dumps(payload)
                result=await session.call_tool('create_paused_ad',dict(account_id='123',draft=dict(name='Carousel HTTP',adset_id='222',creative_spec=spec())))
                data=result.model_dump(by_alias=True)
                assert not data.get('isError')
                payload=data.get('structuredContent') or json.loads(data['content'][0]['text'])
                assert payload['status']=='PAUSED'
                assert len(payload['submitted_creative']['object_story_spec']['link_data']['child_attachments'])==2
    try:
        _wait_until_listening(port)
        asyncio.run(exercise())
    finally:
        process.terminate(); process.wait(timeout=10)
