"""Local real-service smoke test with rolled-back SQL data and removed search entries."""
import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, text
from backend.db import SessionLocal, engine
from backend.models import KnowledgeDocument, Tenant
from backend.rag_v3_index import register,index_document
from backend.rag_v3 import run
from backend.rag_v3_release import FEATURES
from backend.rag_v3_search import index
from backend.lexical_index import client


async def main():
    source_id=None
    try:
        async with SessionLocal() as session:
            tenant=await session.scalar(select(Tenant).where(Tenant.slug=='legacy-demo'))
            if tenant is None: tenant=await session.scalar(select(Tenant).where(Tenant.status=='active').limit(1))
            if tenant is None: raise RuntimeError('No active tenant for local smoke test')
            await session.execute(text("SELECT set_config('app.current_tenant_id', :id, true)"),{'id':tenant.id})
            await session.execute(text("SELECT set_config('app.current_company_role','company_admin',true)"))
            document=KnowledgeDocument(tenant_id=tenant.id,title='V3 isolated smoke fixture',version=1,
                module='test',source='smoke.csv',license='synthetic test',body='',index_status='v3_pending')
            session.add(document);await session.flush()
            source=await register(session,document,'smoke.csv',b'id,limit,condition\nENGINE-42,17,Must not exceed the limit\n')
            source_id=source.id
            await index_document(session,source); await session.flush()
            # Permissive fixture-only model tests plumbing, not relevance accuracy.
            config={'classifier':{'weights':dict.fromkeys(FEATURES,0.0),'intercept':1.0,'threshold':0.0}}
            result=await run(session,tenant.id,'What is the limit for ENGINE-42?',[],600,config)
            assert result['chunks'] and result['diagnostics']['final_tokens']<=600
            assert '17' in result['evidence_text']
            print(json.dumps({'smoke':'passed','tokens':result['diagnostics']['final_tokens'],
                'units':len(result['chunks']),'source_ready':source.phase,'quality_evaluation':False}))
            await session.rollback()
    finally:
        if source_id:
            async with client() as http:
                response=await http.post('/'+index()+'/_delete_by_query?refresh=true',json={'query':{'term':{'document_id':source_id}}})
                response.raise_for_status()
        await engine.dispose()


if __name__=='__main__':
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner: runner.run(main())
