"""Offline calibration and locked validation; never invoked by user queries."""
import math
import statistics
from backend.rag_v3_release import FEATURES, classify


def calibrate(rows, epochs=800):
    if not rows or any(r['split']!='calibration' for r in rows):
        raise ValueError('Training accepts calibration rows only')
    if {r['relevant'] for r in rows}!={True,False}: raise ValueError('Both label classes required')
    weights={k:0.0 for k in FEATURES}; intercept=0.0
    for _ in range(epochs):
        gradient={k:0.0 for k in FEATURES}; bias=0.0
        for row in rows:
            x=row['features']; z=intercept+sum(weights[k]*x[k] for k in FEATURES)
            error=1/(1+math.exp(-max(-60,min(60,z))))-int(row['relevant'])
            bias+=error
            for k in FEATURES: gradient[k]+=error*x[k]
        for k in FEATURES: weights[k]-=.03*(gradient[k]/len(rows)+.001*weights[k])
        intercept-=.03*bias/len(rows)
    model={'weights':weights,'intercept':intercept,'threshold':0.0}
    positive=sorted(classify(r['features'],model)[1] for r in rows if r['relevant'])
    # Highest empirical threshold retaining at least 95% of calibration positives.
    model['threshold']=positive[int(len(positive)*.05)]
    return model


def metrics(rows):
    answerable=[r for r in rows if r['necessary_total']>0]
    noanswer=[r for r in rows if r['necessary_total']==0]
    simple=[r for r in rows if r['kind']=='fact' and r['necessary_total']>0]
    required=sum(r['necessary_total'] for r in answerable)
    ratio=lambda key:sum(r[key] for r in answerable)/required if required else 0
    old=statistics.median(r['old_tokens'] for r in simple) if simple else 0
    new=statistics.median(r['final_tokens'] for r in simple) if simple else 0
    latency=sorted(r['warm_latency_ms'] for r in rows if r.get('old_warm_latency_ms',0)>0)
    old_latency=sorted(r['old_warm_latency_ms'] for r in rows if r.get('old_warm_latency_ms',0)>0)
    p95=max(0,math.ceil(.95*len(latency))-1)
    return {'recall':ratio('recalled_necessary'),'coverage':ratio('covered_necessary'),
        'old_coverage':ratio('old_covered_necessary'),
        'noanswer_false_positive':sum(bool(r['returned_relevant']) for r in noanswer)/len(noanswer) if noanswer else 1,
        'token_reduction':1-new/old if old else -1,
        'latency_ratio_p95':latency[p95]/old_latency[p95] if latency and len(latency)==len(rows) else 1e9,
        'violations':sum(r['permission_leaks']+r['source_mismatches']+r['fact_corruptions']+
            int(r['final_tokens']>r['budget']) for r in rows)}


def metric_failures(m):
    return [name for name,ok in {
        'recall_95':m['recall']>=.95,'coverage_90_and_baseline':m['coverage']>=max(.90,m['old_coverage']),
        'noanswer_fp_5':m['noanswer_false_positive']<=.05,'token_reduction_25':m['token_reduction']>=.25,
        'latency_1_5':m['latency_ratio_p95']<=1.5,'zero_violations':m['violations']==0}.items() if not ok]


def validate_manifest(cases):
    errors=[]
    if len(cases)<120: errors.append('fewer_than_120_questions')
    if len({c['id'] for c in cases})!=len(cases): errors.append('duplicate_question_ids')
    if len({c['domain'] for c in cases})<6: errors.append('fewer_than_6_domains')
    if {c['format'] for c in cases}!={'md','txt','docx','csv','json'}: errors.append('format_coverage')
    if {c['language'] for c in cases}!={'zh','en'}: errors.append('language_coverage')
    if any(c.get('review_status')!='independently_reviewed' for c in cases): errors.append('independent_review_pending')
    train=[c for c in cases if c['split']=='calibration']; valid=[c for c in cases if c['split']=='validation']
    for key in ('domain','document_group'):
        if {c[key] for c in train}&{c[key] for c in valid}: errors.append(key+'_split_leak')
    if not train or not valid: errors.append('missing_split')
    if sum('XL-107' in str(c) for c in cases)>len(cases)*.1: errors.append('demo_overrepresented')
    return errors


def evaluate(cases, rows):
    from backend.rag_v3_policy import PIPELINE_SCHEMA
    errors=validate_manifest(cases)
    if any(row.get('pipeline_schema') != PIPELINE_SCHEMA for row in rows):
        errors.append('pipeline_version_mismatch')
    expected={c['id'] for c in cases if c['split']=='validation'}
    if {r['id'] for r in rows}!=expected or len(rows)!=len(expected): errors.append('incomplete_locked_validation')
    case_map={c['id']:c for c in cases}
    for row in rows:
        if row['id'] not in case_map or row['format']!=case_map[row['id']]['format']:
            errors.append('run_source_format_mismatch')
        count_keys=('necessary_total','recalled_necessary','covered_necessary','old_covered_necessary','permission_leaks','source_mismatches','fact_corruptions','old_tokens','final_tokens')
        if any(type(row[k]) is not int or row[k]<0 for k in count_keys): raise ValueError('invalid observation counts')
        if any(row[k]>row['necessary_total'] for k in ('recalled_necessary','covered_necessary','old_covered_necessary')):
            raise ValueError('observed facts exceed annotated total')
        if row['budget'] not in (600,1200,1800): raise ValueError('invalid evidence budget')
    overall=metrics(rows) if rows else {}
    per_format={}
    if rows:
        errors.extend(metric_failures(overall))
        for fmt in ('md','txt','docx','csv','json'):
            subset=[r for r in rows if r['format']==fmt]
            if not subset: errors.append('missing_format_'+fmt); continue
            per_format[fmt]=metrics(subset)
            errors.extend(fmt+':'+e for e in metric_failures(per_format[fmt]))
    return {'passed':not errors,'failures':errors,'metrics':overall,'per_format':per_format,
            'pipeline_schema': PIPELINE_SCHEMA}


def compare_models(current, multilingual):
    """Compare verified reports on the same locked questions and machine profile."""
    if current['cases_sha256']!=multilingual['cases_sha256'] or current['environment']!=multilingual['environment']:
        raise ValueError('Model comparison requires paired corpus and environment')
    eligible=[]
    for name,report in (('current',current),('multilingual',multilingual)):
        if not report['passed'] or metric_failures(report['metrics']): continue
        if any(metric_failures(report['per_format'][f]) for f in ('md','txt','csv','json','docx')): continue
        macro=sum(report['by_language'][lang]['recall'] for lang in ('zh','en'))/2
        eligible.append((macro,name,report))
    if not eligible: return {'passed':False,'reason':'Neither model combination passes validation','selected':None}
    # A negligible recall difference does not justify switching away from the current pair.
    eligible.sort(key=lambda row:(round(row[0],3),row[1]=='current'),reverse=True)
    winner=eligible[0]
    return {'passed':True,'selected':winner[1],'macro_recall':winner[0],'identity':winner[2]['identity']}
