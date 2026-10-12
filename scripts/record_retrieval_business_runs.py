"""Collect a frozen business corpus using the shared recording contract."""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.record_retrieval_boundary_runs import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("evaluations/retrieval/business/cases.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--budget", type=int, choices=(600, 1200, 1800), default=1200)
    args = parser.parse_args()
    from backend.retrieval_release import RELEASE_FORMATS

    args.formats = RELEASE_FORMATS
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main(args)))
