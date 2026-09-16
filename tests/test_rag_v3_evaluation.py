import json
from pathlib import Path
import pytest
from fastapi import HTTPException
from backend.rag_v3_evaluation import calibrate, validate_manifest, metrics, metric_failures
from backend.rag_v3_release import FEATURES, release


def test_synthetic_corpus_is_not_release_ready():
    cases=[json.loads(line) for line in Path('evaluations/v3/cases.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(cases)==120
    assert validate_manifest(cases)==['independent_review_pending']


def test_cannot_calibrate_on_locked_validation():
    with pytest.raises(ValueError): calibrate([{'split':'validation','relevant':True}])
    rows=[]
    for label in (False,True):
        for _ in range(20): rows.append({'split':'calibration','relevant':label,
            'features':{k:float(label) for k in FEATURES}})
    model=calibrate(rows)
    assert set(model['weights'])==set(FEATURES) and 0<=model['threshold']<=1


def test_failures_cannot_be_hidden_by_low_tokens():
    row={'kind':'fact','necessary_total':10,'recalled_necessary':9,'covered_necessary':8,'old_covered_necessary':10,
        'old_tokens':1000,'final_tokens':100,'warm_latency_ms':100,'old_warm_latency_ms':100,
        'returned_relevant':True,'permission_leaks':1,'source_mismatches':0,'fact_corruptions':0,'budget':600}
    failures=metric_failures(metrics([row]))
    assert 'recall_95' in failures and 'coverage_90_and_baseline' in failures and 'zero_violations' in failures


def test_missing_release_is_explicit_failure(monkeypatch,tmp_path):
    from backend.config import get_settings
    monkeypatch.setattr(get_settings(),'rag_v3_release',str(tmp_path/'missing.json'))
    with pytest.raises(HTTPException) as exc: release()
    assert exc.value.status_code==503


def test_old_pipeline_release_cannot_authorize_new_evidence_policy(monkeypatch, tmp_path):
    from backend.config import get_settings
    path = tmp_path/'release.json'
    path.write_text(json.dumps({'pipeline_schema': 1}), encoding='utf-8')
    monkeypatch.setattr(get_settings(), 'rag_v3_release', str(path))
    with pytest.raises(HTTPException) as exc:
        release()
    assert exc.value.status_code == 503 and '重新评测' in exc.value.detail
