"""Side-index BM25. Authorization IDs are supplied by the SQL authority."""
import json
from backend.config import get_settings
from backend.lexical_index import client


def index():
    return get_settings().opensearch_index + '-v3'


async def publish(source, units):
    async with client() as http:
        response = await http.put('/'+index(), json={'mappings': {'properties': {
            'tenant_id': {'type': 'keyword'}, 'document_id': {'type': 'keyword'},
            'lexemes': {'type': 'text', 'analyzer': 'whitespace', 'similarity': 'BM25'}}}})
        if response.status_code != 200 and not (response.status_code == 400 and
                response.json().get('error', {}).get('type') == 'resource_already_exists_exception'):
            response.raise_for_status()
        # Each version has its own document ID. SQL readiness is the publication barrier.
        lines = []
        for unit in units:
            lines.extend([json.dumps({'index': {'_index': index(), '_id': unit.id}}),
                json.dumps({'tenant_id': source.tenant_id, 'document_id': source.id,
                            'lexemes': unit.lexemes}, ensure_ascii=False)])
        response = await http.post('/_bulk?refresh=wait_for', content='\n'.join(lines)+'\n',
                                   headers={'Content-Type': 'application/x-ndjson'})
        response.raise_for_status()
        if response.json().get('errors', True):
            raise RuntimeError('V3 index publication failed')


async def search(tenant_id, document_ids, query, *, limit=30):
    if not document_ids: return []
    async with client() as http:
        response = await http.post('/'+index()+'/_search', json={'size': min(max(limit, 1), 60), '_source': False,
            'query': {'bool': {'filter': [{'term': {'tenant_id': tenant_id}},
                {'terms': {'document_id': document_ids}}], 'must': [{'match': {'lexemes': query}}]}}})
        response.raise_for_status()
        return [(h['_id'], h['_score']) for h in response.json()['hits']['hits']]
