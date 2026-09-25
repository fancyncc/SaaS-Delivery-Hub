"""Check the local demo's deployed V3 route without printing credentials."""
import json
from pathlib import Path

import httpx


def main():
    root = Path(__file__).resolve().parents[1] / 'data/demo_workspace'
    account = json.loads((root / 'credentials.json').read_text(encoding='utf-8'))['admin']
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    with httpx.Client(base_url='http://127.0.0.1:8080', trust_env=False, timeout=180) as client:
        def post(path, payload=None):
            response = client.post(path, json=payload,
                headers={'X-CSRF-Token': client.cookies.get('saas_csrf', '')})
            response.raise_for_status()
            return response.json()['data']
        post('/api/auth/login', {'username': account['username'], 'password': account['password']})
        post('/api/auth/spaces/' + manifest['tenant_id'] + '/switch')
        for question in ['售后工单的属性有哪些', 'XL-107 P1 首次响应时限是多少？', '如何烤制草莓蛋糕？', '你好']:
            data = post('/api/knowledge/inspect', {'question': question})
            assert data['pipeline'] == 'v3'
            report = {key: data.get(key) for key in (
                'question', 'pipeline', 'status', 'pending_documents', 'relevance_mode', 'quality_calibrated')}
            report['chunks'] = [{'title': h['title'], 'text': h['text'], 'score': h.get('rerank_score')} for h in data['chunks']]
            report['final_tokens'] = data.get('diagnostics', {}).get('final_tokens', 0)
            assert report['final_tokens'] <= 1200
            print(json.dumps(report, ensure_ascii=True), flush=True)
            if question == '你好':
                assert data['status'] == 'not_needed'
            elif '蛋糕' in question:
                assert data['status'] == 'insufficient_relevance' and not data['chunks']
            else:
                assert data['chunks'], data['status']
                evidence = data['evidence_text']
                if 'P1' in question:
                    assert '15' in evidence and 'XL-107' in evidence
                else:
                    assert 'ticket_id' in evidence and 'priority' in evidence


if __name__ == '__main__':
    main()
