"""Assign only manifest-owned demo documents to their authored project scope."""
import json
import sqlite3
from pathlib import Path

from demo_documents import all_documents

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'data/demo_workspace/manifest.json').read_text(encoding='utf-8'))
authored = {d['key']: d for d in all_documents()}
with sqlite3.connect(root / 'saas_agent.db') as db:
    changed = 0
    for record in manifest['documents']:
        doc = authored[record['key']]
        project_id = manifest['projects'][doc['project']]['id'] if doc.get('project') else None
        row = db.execute('SELECT tenant_id, title, version, project_id FROM knowledge_documents WHERE id=?', (record['id'],)).fetchone()
        assert row and row[:3] == (manifest['tenant_id'], record['title'], record['version']), 'Manifest ownership mismatch'
        assert row[3] in (None, project_id), 'Existing scope differs from authored scope'
        db.execute('UPDATE knowledge_documents SET project_id=? WHERE id=? AND tenant_id=?', (project_id, record['id'], manifest['tenant_id']))
        changed += bool(project_id)
    db.commit()
print(f'Assigned {changed} project documents; company-wide documents remain shared.')
