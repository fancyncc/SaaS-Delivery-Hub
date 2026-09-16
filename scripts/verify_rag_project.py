"""Verify the new project's live inspection endpoint without LLM calls."""
import json
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'data/demo_workspace/XL-107'
CASES = [
    ('XL-107 项目首期覆盖多少用户和设备？', ['96', '320']),
    ('XL-107 客户 48 小时未回复能自动结案吗？', ['48', '结案']),
    ('XL-107 P1 首次响应时限是多少？', ['15']),
    ('XL-107 项目上线验收需要多少条用例通过？', ['57']),
    ('XL-107 项目回滚触发条件是什么？', ['5%', '10']),
]


def main():
    manifest = json.loads((OUTPUT / 'manifest.json').read_text(encoding='utf-8'))
    credentials = json.loads((ROOT / 'data/demo_workspace/credentials.json').read_text(encoding='utf-8'))
    results = []
    with httpx.Client(base_url='http://127.0.0.1:8000', trust_env=False, timeout=120) as client:
        def call(path, payload=None):
            r = client.post(path, json=payload, headers={'X-CSRF-Token': client.cookies.get('saas_csrf', ''), 'Idempotency-Key': str(uuid4())})
            if r.status_code != 200:
                raise RuntimeError(f'HTTP {r.status_code}: {path}')
            return r.json()['data']
        account = credentials['admin']
        call('/api/auth/login', {'username': account['username'], 'password': account['password']})
        call(f"/api/auth/spaces/{manifest['tenant_id']}/switch")
        for question, expected in CASES:
            data = call('/api/knowledge/inspect', {'question': question, 'project_id': manifest['project_id']})
            body = '\n'.join(c['text'] for c in data['chunks'])
            matched = all(term in body for term in expected)
            assert data['decision_mode'] == 'rules', 'unexpected LLM routing mode'
            results.append({'question': question, 'status': data['status'], 'expected_terms_found': matched,
                'titles': [c['title'] for c in data['chunks']]})
            print(f"Query {len(results)}: {data['status']}, expected_terms={matched}", flush=True)
        data = call('/api/knowledge/inspect', {'question': '你好', 'project_id': manifest['project_id']})
        assert data['status'] == 'not_needed' and not data['chunks']
    (OUTPUT / 'verification.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    assert all(r['expected_terms_found'] for r in results), 'Review retrieval results'
    print('Five real retrieval checks and greeting skip passed.')


if __name__ == '__main__':
    main()
