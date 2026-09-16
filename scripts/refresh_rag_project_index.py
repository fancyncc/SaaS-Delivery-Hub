"""Rebuild only this fixture's stale index identities using current settings."""
import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from backend.db import SessionLocal, engine
from backend.knowledge import index_identity
from backend.retrieval_sources import index_source
from backend.retrieval_sources_models import RetrievalSource


async def main():
    root = Path(__file__).resolve().parents[1] / 'data/demo_workspace'
    fixture = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    manifest = json.loads((root / 'XL-107/manifest.json').read_text(encoding='utf-8'))
    for doc in manifest['documents']:
        async with SessionLocal() as session:
            session.info['rls_context'] = {'app.current_user_id': fixture['users']['admin'],
                'app.current_tenant_id': manifest['tenant_id'], 'app.current_company_role': 'company_admin'}
            source = await session.scalar(select(RetrievalSource).where(
                RetrievalSource.tenant_id == manifest['tenant_id'],
                RetrievalSource.project_id == manifest['project_id'],
                RetrievalSource.kind == 'knowledge', RetrievalSource.origin_id == doc['id']).with_for_update())
            assert source is not None
            if source.phase != 'ready' or source.index_identity != index_identity():
                source.generation += 1
                await index_source(session, source)
                await session.commit()
                print(f'Rebuilt {doc["id"]}', flush=True)
    await engine.dispose()


if __name__ == '__main__':
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main())
