"""Verify the running local assistant without leaving test conversations behind."""
import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--case', choices=('all', 'platform_manual', 'runtime', 'api'), default='all')
    args = parser.parse_args()
    report = {'checks': [], 'production_acceptance': False}
    root = Path(__file__).resolve().parents[1] / 'data' / 'demo_workspace'
    account = json.loads((root / 'credentials.json').read_text(encoding='utf-8'))['admin']
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8080', trust_env=False, timeout=180) as client:
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
                if args.case != 'all' and args.case != kind:
                    continue
                result = await client.post(f"/api/chat/conversations/{chat['id']}/messages", json={'question': question, 'expected_version': chat['version'], 'request_id': str(uuid4())})
                if not result.is_success:
                    detail = result.json().get('detail')
                    report['failed_request'] = {'question': question, 'expected_kind': kind,
                        'http_status': result.status_code,
                        'reason': detail if isinstance(detail, str) else 'request_validation_failed'}
                    if args.output:
                        args.output.parent.mkdir(parents=True, exist_ok=True)
                        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                result.raise_for_status()
                chat = result.json()['data']
                message = chat['messages'][-1]
                citations = message['citations']
                check = {'question': question, 'expected_kind': kind, 'mode': message['mode'],
                         'citation_kinds': [c['kind'] for c in citations], 'steps': message['steps']}
                report['checks'].append(check)
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                if kind == 'api':
                    assert any(c['id'].startswith('api:') and '4000' in c['text'] for c in citations)
                else:
                    citation = next((c for c in citations if c['kind'] == kind), None)
                    assert citation, f"Missing {kind} citation; mode={message['mode']}, kinds={check['citation_kinds']}"
                    if kind == 'runtime':
                        live = json.loads(citation['text'])['projects'][0]
                        assert live['run']['status'] == 'preparing_materials'
                        assert live['imports'][0]['error_count'] > 0
                check['passed'] = True
                if args.output:
                    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(f'Live verification passed: {kind}', flush=True)
        finally:
            result = await client.delete(f"/api/chat/conversations/{chat['id']}")
            result.raise_for_status()


if __name__ == '__main__':
    asyncio.run(main())
