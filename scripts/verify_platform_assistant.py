"""Verify the running local assistant without leaving test conversations behind."""
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx


async def main():
    root = Path(__file__).resolve().parents[1] / 'data' / 'demo_workspace'
    account = json.loads((root / 'credentials.json').read_text(encoding='utf-8'))['admin']
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000', trust_env=False, timeout=30) as client:
        login = await client.post('/api/auth/login', json={'username': account['username'], 'password': account['password']})
        login.raise_for_status()
        client.headers['X-CSRF-Token'] = client.cookies['saas_csrf']
        profile = await client.get('/api/chat/assistant')
        profile.raise_for_status()
        assert len(profile.json()['data']['knowledge_topics']) == 18
        result = await client.post('/api/chat/conversations', json={})
        result.raise_for_status()
        chat = result.json()['data']
        try:
            checks = [
                ('这个平台有哪些功能，各自从哪里进入？', 'platform_manual'),
                ('XL-104 当前卡在哪一步，导入出现了什么错误？', 'runtime'),
                ('POST /api/chat/conversations/{identifier}/messages 接口请求格式和字段限制是什么？', 'api'),
            ]
            for question, kind in checks:
                result = await client.post(f"/api/chat/conversations/{chat['id']}/messages", json={'question': question, 'expected_version': chat['version'], 'request_id': str(uuid4())})
                result.raise_for_status()
                chat = result.json()['data']
                citations = chat['messages'][-1]['citations']
                if kind == 'api':
                    assert any(c['id'].startswith('api:') and '4000' in c['text'] for c in citations)
                else:
                    citation = next(c for c in citations if c['kind'] == kind)
                    if kind == 'runtime':
                        live = json.loads(citation['text'])['projects'][0]
                        assert live['run']['status'] == 'preparing_materials'
                        assert live['imports'][0]['error_count'] > 0
                print(f'Live verification passed: {kind}', flush=True)
        finally:
            result = await client.delete(f"/api/chat/conversations/{chat['id']}")
            result.raise_for_status()


if __name__ == '__main__':
    asyncio.run(main())
