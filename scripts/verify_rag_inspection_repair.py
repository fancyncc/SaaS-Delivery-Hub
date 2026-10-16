"""Verify the two reported questions against the existing, authenticated workspace."""
import argparse
import json
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://localhost:8080')
    parser.add_argument('--output', default='data/acceptance/rag-inspection-repair.json')
    parser.add_argument('--baseline', action='store_true')
    args = parser.parse_args()
    credentials = json.loads((ROOT / 'data/demo_workspace/credentials.json').read_text(encoding='utf-8'))
    manifest = json.loads((ROOT / 'data/demo_workspace/XL-107/manifest.json').read_text(encoding='utf-8'))
    cases = [
        ('成员迁移流程是怎样的', None, ['配置审批', 'CSV', '审批通过', '回读']),
        ('触发回滚的情况有哪些', manifest['project_id'], ['数据泄露', '10', '5%', '30']),
    ]
    results = []
    with httpx.Client(base_url=args.base_url, trust_env=False, timeout=180) as client:
        def post(path, payload=None):
            response = client.post(path, json=payload, headers={
                'X-CSRF-Token': client.cookies.get('saas_csrf', ''), 'Idempotency-Key': str(uuid4())})
            response.raise_for_status()
            return response.json()['data']

        account = credentials['admin']
        post('/api/auth/login', {'username': account['username'], 'password': account['password']})
        post(f"/api/auth/spaces/{manifest['tenant_id']}/switch")
        for question, project, expected in cases:
            result = post('/api/knowledge/inspect', {'question': question, 'project_id': project, 'evidence_budget': 1200})
            body = '\n'.join(chunk['text'] for chunk in result['chunks'])
            original_matches = []
            for chunk in result['chunks']:
                response = client.get(f"/api/knowledge/{chunk['origin_id']}")
                response.raise_for_status()
                original_matches.append(chunk['text'] in response.json()['data']['body'])
            result['verification'] = {
                'expected_terms': expected, 'passed': all(term in body for term in expected),
                'original_text_verified': bool(original_matches) and all(original_matches),
            }
            results.append(result)
            print(json.dumps({'question': question, 'status': result['status'], 'verification': result['verification'],
                              'titles': [c['title'] for c in result['chunks']],
                              'diagnostics': {k: v for k, v in result.get('diagnostics', {}).items() if k != 'candidates'}}, ensure_ascii=False), flush=True)
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    if not args.baseline:
        assert all(result['verification']['passed'] and result['status'] in {'evidence_found', 'budget_limited'} for result in results)
        assert all(result.get('retrieval_executed') is True for result in results)
        assert all(result['diagnostics']['rerank_count'] > 0 for result in results)
        assert all(result['verification']['original_text_verified'] for result in results)
        assert all(result['diagnostics']['final_tokens'] <= 1200 for result in results)


if __name__ == '__main__':
    main()
