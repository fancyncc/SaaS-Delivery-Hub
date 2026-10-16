"""Inspect citation invariants in a disposable in-process real-model request.

No serving process is patched. Credentials use stdin and never enter the output.
Only source IDs, numbers and boolean validation results are saved, not answers.
"""
import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).parent / "evidence"
creds = json.loads((ROOT / "data/demo_workspace/credentials.json").read_bytes())["admin"]
tenant = json.loads((ROOT / "data/demo_workspace/manifest.json").read_bytes())["tenant_id"]
CHILD = r'''
import asyncio,json,re,sys
import httpx
from uuid import uuid4
from backend import intelligence
from backend.chat import ChatAnswer
from backend.main import app
from backend.db import engine
payload=json.load(sys.stdin)
diagnostics=[]
original=intelligence.structured
async def observed(task,source,schema,**kwargs):
    result=await original(task,source,schema,**kwargs)
    if schema is ChatAnswer:
        sources=source['evidence']
        mapping={}
        for i,s in enumerate(sources,1):mapping.setdefault(s['id'],[]).append(i)
        diagnostics.append({'source_count':len(sources),
            'source_id_numbers':mapping,
            'source_kinds':[s.get('kind') for s in sources],
            'cited_numbers':sorted({int(n) for n in re.findall(r'\[(\d+)\]',result.answer)}),
            'provided_ids_known':[value in mapping for value in result.citation_ids],
            'provided_ids_are_kinds':[any(value==s.get('kind') for s in sources) for value in result.citation_ids],
            'expected_numbers_from_ids':[i for i,s in enumerate(sources,1) if s['id'] in result.citation_ids],
            'provided_ids':result.citation_ids})
    return result
intelligence.structured=observed
async def main():
    chat=None
    report={}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test',timeout=180) as client:
        async def post(path,body):
            r=await client.post(path,json=body,headers={'X-CSRF-Token':client.cookies.get('saas_csrf','')})
            r.raise_for_status();return r.json()['data']
        try:
            await post('/api/auth/login',{'username':payload['username'],'password':payload['password']})
            await post('/api/auth/spaces/'+payload['tenant_id']+'/switch',{})
            chat=await post('/api/chat/conversations',{})
            r=await client.post('/api/chat/conversations/'+chat['id']+'/messages',json={
                'question':'XL-104 当前卡在哪一步，导入出现了什么错误？',
                'expected_version':chat['version'],'request_id':str(uuid4())},
                headers={'X-CSRF-Token':client.cookies.get('saas_csrf','')})
            report={'status':r.status_code,'detail':r.json().get('detail'),'attempts':diagnostics}
            if r.is_success:
                citations=r.json()['data']['messages'][-1]['citations']
                report['real_source_identity_preserved']=all(not c['id'].startswith('evidence-') for c in citations)
        finally:
            if chat:
                r=await client.delete('/api/chat/conversations/'+chat['id'],headers={'X-CSRF-Token':client.cookies.get('saas_csrf','')})
                report['test_conversation_deleted']=r.is_success
            await engine.dispose()
    print(json.dumps(report,ensure_ascii=False))
asyncio.run(main())
'''
parser=argparse.ArgumentParser()
parser.add_argument('--candidate',action='store_true')
args=parser.parse_args()
command=(["docker", "compose", "run", "--rm", "--no-deps", "-T", "api", "python", "-c", CHILD]
         if args.candidate else ["docker", "compose", "exec", "-T", "api", "python", "-c", CHILD])
result = subprocess.run(command,
    input=json.dumps({"username": creds["username"], "password": creds["password"], "tenant_id": tenant}),
    capture_output=True, text=True, encoding="utf-8", timeout=400)
if result.returncode:
    print("DIAGNOSTIC_PROCESS_FAILED")
    raise SystemExit(1)
report = json.loads(result.stdout.strip().splitlines()[-1])
(EVIDENCE / ("runtime-citation-candidate.json" if args.candidate else "runtime-citation-diagnostic.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
