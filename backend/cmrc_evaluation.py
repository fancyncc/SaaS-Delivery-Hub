"""CMRC public benchmark preparation and source-position-aware scoring.

EM/F1 follows CMRC's v6 evaluator (CC BY-SA 4.0, Yiming Cui et al.).
F1 uses the longest *contiguous* matching token sequence, not bag-of-words F1.
This benchmark does not issue production acceptance or independent review receipts.
"""

import math
import random
import re
from pathlib import Path

from backend.benchmark_tokenizer import tokenizer_metadata, word_tokenize
from backend.evaluation_artifacts import canonical, digest, read_rows, write_once

REVISION = "c0eb1b6ba219847457e6af3180da722bbeb656af"
SOURCE_SHA256 = "86000878d2e8b535e33b36161a3032d74398881d8ee3ae16b2b22f1a48a99cc6"
UPSTREAM = "https://github.com/ymcui/cmrc2018"
PUNCTUATION = frozenset([
    "-", ":", "_", "*", "^", "/", "\\", "~", "`", "+", "=", "，", "。", "：", "？",
    "！", "“", "”", "；", "’", "《", "》", "……", "·", "、", "「", "」", "（", "）",
    "－", "～", "『", "』",
])


def remove_punctuation(value):
    return "".join(c for c in str(value).lower().strip() if c not in PUNCTUATION)


def mixed_segmentation(value):
    tokens, pending = [], ""
    for char in str(value).lower().strip():
        if char in PUNCTUATION:
            continue
        if re.search(r"[\u4e00-\u9fa5]", char):
            if pending:
                tokens.extend(word_tokenize(pending))
                pending = ""
            tokens.append(char)
        else:
            pending += char
    if pending:
        tokens.extend(word_tokenize(pending))
    return tokens


def longest_match(a, b):
    """Equivalent to the official find_lcs with linear auxiliary memory."""
    previous, best = [0] * (len(b) + 1), 0
    for left in a:
        current = [0] * (len(b) + 1)
        for j, right in enumerate(b, 1):
            if left == right:
                current[j] = previous[j - 1] + 1
                best = max(best, current[j])
        previous = current
    return best


def answer_scores(answers, prediction):
    predicted = mixed_segmentation(prediction)
    scores = []
    for answer in answers:
        reference = mixed_segmentation(answer)
        overlap = longest_match(reference, predicted)
        scores.append(2 * overlap / (len(reference) + len(predicted)) if overlap else 0.0)
    return {
        "em": float(any(remove_punctuation(a) == remove_punctuation(prediction) for a in answers)),
        "f1": max(scores, default=0.0),
    }


def prepare(raw, output, *, limit=200, seed=2018, revision=REVISION):
    """Preserve official labels, including bad offsets; never silently repair them."""
    import json

    if limit < 0 or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid sample limit or source revision")
    data = json.loads(raw)
    all_cases, contexts = [], {}
    for article in data["data"]:
        for paragraph in article["paragraphs"]:
            context = paragraph["context"]
            document = "documents/" + digest(context.encode("utf-8")) + ".txt"
            contexts[document] = context
            for qa in paragraph["qas"]:
                spans = []
                for annotation in qa["answers"]:
                    text, start = annotation["text"], annotation["answer_start"]
                    valid = (type(start) is int and start >= 0 and bool(text)
                             and context[start:start + len(text)] == text)
                    spans.append({**annotation, "valid_position": valid})
                all_cases.append({
                    "id": qa["id"], "question": qa["question"], "document": document,
                    "title": article.get("title", "CMRC paragraph"), "answers": spans,
                })
    if not all_cases or len({c["id"] for c in all_cases}) != len(all_cases):
        raise ValueError("Empty dataset or duplicate question ids")
    selected = (random.Random(seed).sample(all_cases, limit)
                if limit and limit < len(all_cases) else all_cases)
    selected.sort(key=lambda c: c["id"])
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "documents").mkdir()
    for document in sorted({c["document"] for c in selected}):
        (output / document).write_bytes(contexts[document].encode("utf-8"))
    (output / "cmrc2018_dev.json").write_bytes(raw)
    cases_raw = b"".join(canonical(c) + b"\n" for c in selected)
    (output / "cases.jsonl").write_bytes(cases_raw)
    manifest = {
        "schema": 1, "dataset": "CMRC 2018", "split": "public_development",
        "source_url": f"{UPSTREAM}/blob/{revision}/squad-style-data/cmrc2018_dev.json",
        "upstream_revision": revision, "license": "CC BY-SA 4.0",
        "attribution": "Yiming Cui et al., A Span-Extraction Dataset for Chinese Machine Reading Comprehension (2019)",
        "raw_sha256": digest(raw), "cases_sha256": digest(cases_raw),
        "seed": seed, "selection": "all" if len(selected) == len(all_cases) else "seeded_random_sample",
        "available_questions": len(all_cases), "question_count": len(selected),
        "document_count": len({c["document"] for c in selected}),
        "corpus_scope": "unique original paragraphs associated with selected questions",
        "sources_sha256": {name: digest((output / name).read_bytes())
                           for name in sorted({c["document"] for c in selected})},
        "invalid_answer_annotations": sum(not a["valid_position"] for c in selected for a in c["answers"]),
        "questions_without_valid_position": sum(not any(a["valid_position"] for a in c["answers"]) for c in selected),
        "notes": ["Public development benchmark, not an unseen-model holdout.",
                  "RAG corpus conversion is not the original passage-given leaderboard task.",
                  "Original answer labels and offsets are retained without manual correction."],
    }
    write_once(output / "manifest.json", manifest)
    return manifest


def load_corpus(directory):
    import json

    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_bytes())
    if (digest((directory / "cases.jsonl").read_bytes()) != manifest["cases_sha256"]
            or digest((directory / "cmrc2018_dev.json").read_bytes()) != manifest["raw_sha256"]):
        raise ValueError("CMRC labels or original dataset changed")
    for name, checksum in manifest["sources_sha256"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or digest(path.read_bytes()) != checksum:
            raise ValueError("CMRC source changed")
    cases = read_rows(directory / "cases.jsonl")
    if (len(cases) != manifest["question_count"] or len({c["id"] for c in cases}) != len(cases)
            or {c["document"] for c in cases} != set(manifest["sources_sha256"])):
        raise ValueError("CMRC case registry mismatch")
    return cases, manifest


def compact_prediction(answer):
    """Remove only application citation markers; no gold-guided extraction."""
    return re.sub(r"\[\d+\]", "", answer).strip()


def normalized_with_positions(text):
    pairs = [(i, char) for i, char in enumerate(text) if not char.isspace()]
    return "".join(char for _, char in pairs), [i for i, _ in pairs]


def source_intervals(context, text):
    """Locate verbatim unit text allowing only whitespace introduced by splitting.

    Ambiguous repeated text is not credited: a unit without unique alignment
    cannot establish which of the annotated source positions it contains.
    """
    normalized, positions = normalized_with_positions(context)
    needle, _ = normalized_with_positions(text)
    if not needle:
        return []
    start = normalized.find(needle)
    if start < 0 or normalized.find(needle, start + 1) >= 0:
        return []
    return [(positions[start], positions[start + len(needle) - 1] + 1)]


def evidence_hit(case, hits, snapshot, corpus):
    valid = [a for a in case["answers"] if a["valid_position"]]
    if not valid:
        return None
    context = (Path(corpus) / case["document"]).read_text(encoding="utf-8")
    for hit in hits:
        document = snapshot["documents"].get(hit.get("document_id"), {})
        if document.get("document") != case["document"]:
            continue
        # Check the actual returned body, not merely its source id or an answer string.
        for start, end in source_intervals(context, hit.get("text", "")):
            if any(start <= a["answer_start"] and end >= a["answer_start"] + len(a["text"])
                   for a in valid):
                return True
    return False


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def score_run(corpus, report, *, input_price=None, output_price=None, currency=None):
    """Errors and unanswered requests remain in EM/F1 and hit-rate denominators."""
    cases, manifest = load_corpus(corpus)
    frozen, rows = report["frozen"], report["rows"]
    if (report.get("frozen_sha256") != digest(canonical(frozen))
            or report.get("rows_sha256") != digest(canonical(rows))
            or frozen["manifest_sha256"] != digest(canonical(manifest))):
        raise ValueError("Unbound or changed CMRC recording")
    expected = frozen["case_ids"]
    if len(set(expected)) != len(expected) or not set(expected) <= {c["id"] for c in cases}:
        raise ValueError("Unknown or duplicate frozen CMRC ids")
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids) or not set(ids) <= set(expected):
        raise ValueError("Unknown or duplicate recorded CMRC ids")
    case_map, row_map = {c["id"]: c for c in cases}, {r["id"]: r for r in rows}
    snapshot = report.get("source_snapshot", {"documents": {}})
    if report.get("source_snapshot_sha256") != digest(canonical(snapshot)):
        raise ValueError("CMRC source snapshot changed")
    for document in snapshot["documents"].values():
        if manifest["sources_sha256"].get(document["document"]) != document["sha256"]:
            raise ValueError("Indexed source hash differs from corpus")
    if report.get("complete") and {d["document"] for d in snapshot["documents"].values()} != set(manifest["sources_sha256"]):
        raise ValueError("Incomplete indexed CMRC corpus")
    scored, evidence_rows, usages = [], [], []
    for qid in expected:
        case, row = case_map[qid], row_map.get(qid, {})
        prediction = compact_prediction(row.get("answer", "")) if row.get("outcome") == "succeeded" else ""
        metrics = answer_scores([a["text"] for a in case["answers"]], prediction)
        stages = row.get("retrieval", {}).get("diagnostics", {}).get("evaluation_stages", {})
        ranked = stages.get("rerank", [])
        hit_rates = {str(k): evidence_hit(case, ranked[:k], snapshot, corpus) for k in (1, 3, 5)}
        selected_hit = evidence_hit(case, row.get("retrieval", {}).get("chunks", []), snapshot, corpus)
        if selected_hit is not None:
            evidence_rows.append({"hits": hit_rates, "selected": selected_hit})
        scored.append({"id": qid, "prediction": prediction, **metrics,
                       "outcome": row.get("outcome", "not_run"),
                       "answer_evidence_hit_at_k": hit_rates, "selected_answer_evidence_hit": selected_hit})
        usage = row.get("usage")
        if isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
                                         for k in ("input_tokens", "output_tokens")):
            usages.append(usage)
    total = len(expected)
    if not total:
        raise ValueError("No frozen CMRC questions")
    token_totals = {k: sum(u[k] for u in usages) for k in ("input_tokens", "output_tokens")}
    cost = None
    if len(usages) == total and input_price is not None and output_price is not None:
        if any(not math.isfinite(p) or p < 0 for p in (input_price, output_price)) or not currency:
            raise ValueError("Explicit finite prices and currency required")
        cost = (token_totals["input_tokens"] * input_price + token_totals["output_tokens"] * output_price) / 1_000_000
    successes = sum(r["outcome"] == "succeeded" for r in scored)
    latencies = {phase: [r[phase] for r in rows if type(r.get(phase)) in (int, float)
                         and math.isfinite(r[phase]) and r[phase] >= 0]
                 for phase in ("retrieval_ms", "generation_ms", "latency_ms")}
    valid_evidence = len(evidence_rows)
    return {
        "schema": 1, "scope": "public_cmrc2018_benchmark", "run_id": report["run_id"],
        "status": report["status"], "complete": (report.get("complete") is True and len(rows) == total
            and successes == total and report.get("test_data_cleaned") is True and not report.get("errors")),
        "production_acceptance": False, "dataset": manifest, "frozen": frozen,
        "question_count": total, "successful_questions": successes,
        "error_rate": (total - successes) / total,
        "metrics": {"em": 100 * sum(r["em"] for r in scored) / total,
                    "f1": 100 * sum(r["f1"] for r in scored) / total,
                    "evidence_eligible_questions": valid_evidence,
                    "evidence_excluded_invalid_positions": total - valid_evidence,
                    "answer_evidence_hit_at_k": {str(k): sum(r["hits"][str(k)] is True for r in evidence_rows) / valid_evidence
                                                 if valid_evidence else None for k in (1, 3, 5)},
                    "selected_answer_evidence_hit_rate": sum(r["selected"] is True for r in evidence_rows) / valid_evidence
                                                         if valid_evidence else None},
        "latency": {phase: {"measured_samples": len(v), "p50_ms": percentile(v, .5),
                            "p95_ms": percentile(v, .95)} for phase, v in latencies.items()},
        "usage": {"measured_questions": len(usages), "complete": len(usages) == total, **token_totals},
        "cost": {"value": cost, "currency": currency, "input_per_million": input_price,
                 "output_per_million": output_price,
                 "status": "measured_usage_priced" if cost is not None else "not_measured"},
        "test_data_cleaned": report.get("test_data_cleaned"), "errors": report.get("errors", []),
        "scoring": {"answer": "CMRC v6 EM / longest contiguous mixed-token F1; best official reference",
                    "nltk_version": None,
                    "tokenizer": tokenizer_metadata(),
                    "evidence": "annotated answer position contained in source-aligned rerank top K / selected body",
                    "prediction": "short answer with numeric citation markers removed; no reference-guided extraction",
                    "denominator": "all frozen questions, including failures; evidence excludes invalid official offsets"},
        "rows": scored,
    }
