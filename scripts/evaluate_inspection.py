"""Run labelled read-only calibration; publish an approval artifact only when gates pass."""
import asyncio
import hashlib
import json
import math
import statistics
from pathlib import Path

from sqlalchemy import select

from backend.db import SessionLocal, engine
from backend.inspection_evidence import inspect, model_identity, serialize_evidence
from backend.retrieval_models import token_counts
from backend.retrieval_sources import authorized_sources
from backend.retrieval_sources_models import RetrievalChunk
from evaluations.inspection_cases import CASES

ROOT = Path(__file__).resolve().parents[1]


def coverage(spans, hits):
    # Complete labelled statements must survive, in their original source wording.
    return sum(any(span in h['text'] for h in hits) for span in spans)


async def main():
    manifest = json.loads((ROOT/'data/demo_workspace/XL-107/manifest.json').read_text(encoding='utf-8'))
    fixture = json.loads((ROOT/'data/demo_workspace/manifest.json').read_text(encoding='utf-8'))
    records = []
    context = {'app.current_tenant_id':manifest['tenant_id'], 'app.current_user_id':fixture['users']['admin'], 'app.current_company_role':'company_admin'}
    # Gather once. Inspection with permissive threshold discovers bounded supplements.
    for case in CASES:
        async with SessionLocal() as session:
            session.info['rls_context'] = context
            scope = [] if case['scope']=='company_only' else [manifest['project_id']]
            result = await inspect(session, manifest['tenant_id'], case['question'], scope, 1800, threshold=-1e30)
            candidates = result['diagnostics']['candidates']
            expected = next((d['id'] for d in manifest['documents'] if Path(d['file']).name.startswith(case['document_prefix'] or 'NOT_FOUND')), None)
            sources = await authorized_sources(session, manifest['tenant_id'], project_ids=scope)
            gold_sources = [s.id for s in sources if s.origin_id==expected]
            gold = list(await session.scalars(select(RetrievalChunk).where(RetrievalChunk.source_id.in_(gold_sources), RetrievalChunk.heading.in_(case['gold_headings'])))) if gold_sources else []
            original = [h for h in candidates if not h['supplemented']][:5]
            records.append({'case':case, 'expected_document':expected, 'gold_ids':[c.citation_id for c in gold], 'candidates':candidates,
                'old_coverage':coverage(case['required_spans'], original), 'old_tokens':(await token_counts([serialize_evidence(original)]))[0]})
            print(f"Collected {case['id']}", flush=True)
    positive_scores = sorted([h['rerank_score'] for r in records if r['case']['split']=='calibration'
        for h in r['candidates'] if h['id'] in r['gold_ids']])
    if not positive_scores:
        raise RuntimeError('No gold evidence found; cannot calibrate')
    threshold = positive_scores[math.floor(len(positive_scores)*.05)]
    totals = {'gold':0,'recalled':0,'facts':0,'old_facts':0,'new_facts':0,'leaks':0,'over_budget':0,'false_positive_cases':0}
    savings = []
    for record in records:
        case = record['case']
        if case['split'] != 'validation':
            continue
        async with SessionLocal() as session:
            session.info['rls_context'] = context
            scope = [] if case['scope']=='company_only' else [manifest['project_id']]
            result = await inspect(session, manifest['tenant_id'], case['question'], scope, 1200, threshold=threshold)
        surviving = {h['id'] for h in result['diagnostics']['candidates'] if h['rerank_score']>=threshold}
        totals['gold'] += len(record['gold_ids'])
        totals['recalled'] += len(set(record['gold_ids']) & surviving)
        totals['facts'] += len(case['required_spans'])
        totals['old_facts'] += record['old_coverage']
        totals['new_facts'] += coverage(case['required_spans'],result['chunks'])
        totals['over_budget'] += result['diagnostics']['final_tokens']>1200
        totals['leaks'] += case['scope']=='company_only' and any(h['document_id'] in {d['id'] for d in manifest['documents']} for h in [*result['chunks'], *result['diagnostics']['candidates']])
        totals['false_positive_cases'] += case['category']=='no_answer' and bool(result['chunks'])
        if case['category']=='fact' and record['old_tokens']:
            savings.append(1-result['diagnostics']['final_tokens']/record['old_tokens'])
        record['result'] = result
        print(f"Validated {case['id']}: {result['status']}", flush=True)
    recall = totals['recalled']/max(1,totals['gold'])
    reduction = statistics.median(savings) if savings else 0
    passed = recall >= .95 and totals['new_facts']>=totals['old_facts'] and reduction>=.25 and not totals['leaks'] and not totals['over_budget'] and not totals['false_positive_cases']
    report = {'identity':model_identity(), 'threshold':threshold, 'passed':passed, 'metrics':totals,
        'validation_recall':recall, 'median_token_reduction':reduction,
        'dataset_sha256':hashlib.sha256(json.dumps(CASES,ensure_ascii=False).encode()).hexdigest(),
        'annotation_review':'agent-authored exact source spans; independent human review pending', 'cases':records}
    (ROOT/'evaluations/inspection_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'evaluations/inspection_calibration.json').write_text(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'cases','identity'}},ensure_ascii=True),flush=True)
    await engine.dispose()


if __name__=='__main__':
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main())
