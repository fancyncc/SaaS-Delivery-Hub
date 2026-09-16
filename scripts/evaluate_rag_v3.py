"""Consume independently reviewed labels and recorded paired runs. No invented scores."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.rag_v3_evaluation import calibrate, evaluate


def read(path): return [json.loads(s) for s in Path(path).read_text(encoding='utf-8').splitlines() if s.strip()]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['calibrate','validate'])
    parser.add_argument('--cases',default='evaluations/v3/cases.jsonl')
    parser.add_argument('--runs',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    rows=read(args.runs)
    if args.action=='calibrate': output=calibrate(rows)
    else:
        cases=read(args.cases); output=evaluate(cases,rows)
        output.update(question_count=len(cases),formats=sorted({c['format'] for c in cases}),
            domain_count=len({c['domain'] for c in cases}),
            cases_sha256=hashlib.sha256(Path(args.cases).read_bytes()).hexdigest(),
            independent_review={'approved':all(c.get('review_status')=='independently_reviewed' for c in cases)},
            model_comparison_passed=False)
        # Model comparison is separately reviewed; this tool cannot claim that it occurred.
    Path(args.output).write_text(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(args.output)


if __name__=='__main__': main()
