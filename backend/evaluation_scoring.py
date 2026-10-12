"""Re-score raw retrieval/answer observations against frozen source labels."""

import math
import re
from collections import Counter

from backend.evaluation_artifacts import canonical, digest

STAGES = ("index", "bm25", "vector", "rrf", "rerank", "relevance", "selected")


def facts(case):
    return list(dict.fromkeys([*case["necessary_facts"], *case.get("protected_conditions", [])]))


def contains_fact(text, fact):
    text, fact = (re.sub(r"\s+", " ", v) for v in (text, fact))
    if re.fullmatch(r"[\w, .-]+", fact, re.ASCII):
        return bool(re.search(r"(?<!\w)" + re.escape(fact) + r"(?!\w)", text))
    return fact in text


def covered_facts(case, hits, snapshot):
    result = set()
    for hit in hits:
        source = snapshot["documents"].get(hit.get("document_id"))
        if source is None or source["document"] != case["document"]:
            continue
        if case.get("required_page") and hit.get("location", {}).get("page") != case["required_page"]:
            continue
        keys = case.get("source_keys", [])
        if keys and not any(re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)",
                                     hit["text"], re.I) for k in keys):
            continue
        for index, fact in enumerate(facts(case)):
            annotations = [s for s in case.get("source_spans", []) if s["fact"] == fact]
            if annotations:
                positions = [hit.get("location", {})]
                positions.extend(snapshot["units"][ref]["location"]
                                 for ref in hit.get("source_chunk_ids", []) if ref in snapshot["units"])
                if not any(all(position.get(key) == value for key, value in location.items())
                           for span in annotations for location in span["locations"] for position in positions):
                    continue
            if contains_fact(hit["text"], fact):
                result.add(index)
    return result


def citation_errors(hits, units, nodes):
    errors = []
    for hit in hits:
        refs = hit.get("source_chunk_ids", [hit["id"]])
        originals = [units.get(ref) for ref in refs]
        if not refs or any(item is None for item in originals):
            errors.append({"id": hit["id"], "reason": "unknown_source_unit"})
            continue
        first = originals[0]
        keys = ("document_id", "origin_id", "title", "version", "heading", "kind", "node_id", "source")
        if any(hit.get(key) != item.get(key) for item in originals for key in keys):
            errors.append({"id": hit["id"], "reason": "source_metadata_mismatch"})
        original_location = first["location"]
        if any(hit.get("location", {}).get(k) != v for k, v in original_location.items()):
            errors.append({"id": hit["id"], "reason": "source_position_mismatch"})
        expected = (
            nodes.get(first["node_id"])
            if hit.get("location", {}).get("reassembled")
            else first["text"]
        )
        if expected is None or hit["text"] != expected:
            errors.append({"id": hit["id"], "reason": "source_text_changed"})
    return errors


def source_errors(hits, snapshot):
    return citation_errors(hits, snapshot["units"], snapshot["nodes"])


def score_observation(case, row, snapshot):
    from backend.retrieval_policy import PIPELINE_SCHEMA

    if row.get("outcome") != "succeeded":
        raise ValueError("Observation did not complete: " + row["id"])
    if row["pipeline_schema"] != PIPELINE_SCHEMA:
        raise ValueError("pipeline_version_mismatch")
    for key in ("id", "format", "language", "split", "kind"):
        if row[key] != case[key]:
            raise ValueError("Observation differs from case: " + key)
    result = row["result"]
    if row["budget"] not in (600, 1200, 1800):
        raise ValueError("invalid_evidence_budget")
    tokens = result.get("diagnostics", {}).get("final_tokens")
    if type(tokens) is not int or tokens < 0:
        raise ValueError("invalid_token_counts")
    latency = row["warm_latency_ms"]
    if type(latency) not in (int, float) or not math.isfinite(latency) or latency < 0:
        raise ValueError("invalid_latency")
    stage_hits = {"index": list(snapshot["units"].values()),
                  **result.get("diagnostics", {}).get("evaluation_stages", {}),
                  "selected": result["chunks"]}
    if any(stage not in stage_hits for stage in STAGES):
        raise ValueError("missing_stage_observations")
    seen = [h for stage in STAGES if stage != "index" for h in stage_hits[stage]]
    errors = source_errors(seen, snapshot)
    invalid = {e["id"] for e in errors}
    stages = {s: sorted(covered_facts(case, [h for h in hits if h["id"] not in invalid], snapshot))
              for s, hits in stage_hits.items()}
    selected = result["chunks"]
    scored = {key: row[key] for key in ("id", "format", "language", "split", "kind", "budget")}
    scored.update(
        pipeline_schema=PIPELINE_SCHEMA, necessary_total=len(facts(case)),
        recalled_necessary=len(stages["rrf"]),
        covered_necessary=len(stages["selected"]), returned_relevant=bool(selected),
        returned_evidence=bool(selected),
        permission_leaks=sum(h.get("document_id") not in snapshot["documents"] for h in seen),
        source_mismatches=len(errors), fact_corruptions=sum(e["reason"] == "source_text_changed" for e in errors),
        citation_errors=errors, final_tokens=result.get("diagnostics", {}).get("final_tokens", 0),
        warm_latency_ms=row["warm_latency_ms"], stages=stages,
        stage_losses={s: {"lost": sorted(set(stages[a]) - set(stages[s])),
                          "recovered": sorted(set(stages[s]) - set(stages[a]))}
                      for a, s in zip(("index", "rrf", "rerank", "relevance"),
                                      ("rrf", "rerank", "relevance", "selected"), strict=True)},
        ranking=ranking_metrics(case, stage_hits["rerank"], snapshot),
    )
    # Reject caller-supplied denominators/counts rather than allowing them to improve scores.
    for key in ("necessary_total", "covered_necessary"):
        if key in row and row[key] != scored[key]:
            raise ValueError("reported_count_mismatch:" + key)
    return scored


def stable_unit_key(hit, snapshot):
    document = snapshot["documents"].get(hit.get("document_id"), {}).get("document")
    return digest(canonical({"document": document, "location": hit.get("location"),
                             "text": hit["text"]}))


def ranking_metrics(case, hits, snapshot, k=10):
    qrels = case.get("qrels")
    if not case.get("qrels_complete") or not isinstance(qrels, dict) or not qrels:
        return {"status": "not_run", "reason": "complete_relevance_labels_required"}
    if any(type(v) is not int or not 0 <= v <= 3 for v in qrels.values()):
        raise ValueError("invalid_qrels")
    keys = list(dict.fromkeys(stable_unit_key(h, snapshot) for h in hits))[:k]
    relevant = {key for key, value in qrels.items() if value > 0}
    if not relevant or any(key not in qrels for key in keys):
        return {"status": "not_run", "reason": "incomplete_judgments"}
    gains = [qrels[key] for key in keys]
    ideal = sorted(qrels.values(), reverse=True)[:k]
    def dcg(values):
        return sum((2 ** value - 1) / math.log2(index + 2) for index, value in enumerate(values))
    return {"status": "passed", "k": k, "recall_at_k": len(set(keys) & relevant) / len(relevant),
            "mrr": next((1 / i for i, key in enumerate(keys, 1) if key in relevant), 0),
            "ndcg": dcg(gains) / dcg(ideal)}


def review_failures(cases, frozen):
    review = frozen.get("review") or {}
    errors = []
    if review.get("cases_sha256") != frozen["corpus"]["cases_sha256"]:
        errors.append("independent_review_unbound")
    approvals = {r.get("id"): r for r in review.get("cases", [])}
    for case in cases:
        receipt = approvals.get(case["id"], {})
        if (case.get("review_status") != "independently_reviewed"
                or receipt.get("status") != "approved" or receipt.get("reviewer_kind") != "human"
                or not receipt.get("reviewed_at") or not receipt.get("reviewer")
                or not receipt.get("author") or receipt["reviewer"] == receipt["author"]
                or receipt.get("label_sha256") != digest(canonical(case))):
            errors.append("independent_review_pending")
            break
    if (review.get("validation_previously_exposed") is not False
            or frozen["corpus"].get("validation_previously_exposed") is True):
        errors.append("fresh_validation_required")
    for case in cases:
        spans = case.get("source_spans", [])
        if any(not any(s.get("fact") == fact and s.get("document") == case["document"]
                       and s.get("locations") and s.get("quotes") for s in spans) for fact in facts(case)):
            errors.append("source_span_review_pending")
            break
    splits = {s: [c for c in cases if c["split"] == s]
              for s in ("development", "calibration", "validation")}
    for field in ("document", "document_group", "content_family", "project_group"):
        sets = {s: {c.get(field, c.get("document_group", c["document"])) for c in rows}
                for s, rows in splits.items()}
        if any(sets[a] & sets[b] for a, b in (("development", "calibration"),
                                            ("development", "validation"),
                                            ("calibration", "validation"))):
            errors.append(field + "_split_leak")
    return errors


def score_answers(case, row, hits):
    """Literal checks diagnose omissions; semantic correctness requires human review."""
    answer = row["answer"]
    required = facts(case)
    citations = row.get("citations", [])
    allowed = {ref for h in hits for ref in h.get("source_chunk_ids", [h["id"]])}
    refs = {c["source_id"] for c in citations}
    forbidden = case.get("forbidden_claims", [])
    return {"id": case["id"], "mode": row["mode"],
            "required": len(required), "covered": sum(contains_fact(answer, fact) for fact in required),
            "conditions_required": len(case.get("protected_conditions", [])),
            "conditions_covered": sum(contains_fact(answer, fact) for fact in case.get("protected_conditions", [])),
            "invalid_citations": len(refs - allowed),
            "missing_citations": bool(required and answer and not refs),
            "forbidden_claims": sum(contains_fact(answer, fact) for fact in forbidden),
            "abstained": row.get("abstained") is True,
            "expected_abstention": not required, "semantic_review": "not_run"}


def answer_report(cases, observations, *, review=None):
    expected = {(c["id"], mode) for c in cases for mode in ("retrieved", "oracle")}
    keys = [(r["id"], r["mode"]) for r in observations]
    errors = []
    if len(set(keys)) != len(keys) or set(keys) != expected:
        errors.append("incomplete_answer_pairs")
    scored = []
    case_map = {c["id"]: c for c in cases}
    for row in observations:
        if row.get("outcome") != "succeeded" or not row.get("provider_model") or not row.get("answer", "").strip():
            errors.append("answer_generation_failed")
            continue
        scored.append(score_answers(case_map[row["id"]], row, row["evidence"]))
    review = review or {}
    approved = (review.get("rows_sha256") == digest(canonical(observations))
                and review.get("reviewer_kind") == "human" and bool(review.get("reviewer"))
                and review.get("reviewer") != review.get("author") and bool(review.get("author"))
                and bool(review.get("reviewed_at")))
    labels = {(r["id"], r["mode"]): r for r in review.get("rows", [])}
    if not approved or set(labels) != expected:
        errors.append("answer_semantic_review_pending")
    else:
        for label in labels.values():
            if not all(label.get(key) is True for key in
                       ("facts_complete", "claims_supported", "conditions_preserved", "abstention_correct")):
                errors.append("answer_semantic_failure")
    for row in scored:
        if row["invalid_citations"] or row["forbidden_claims"] or row["missing_citations"]:
            errors.append("answer_evidence_violation")
        if row["expected_abstention"] and not row["abstained"]:
            errors.append("answer_abstention_failure")
    modes = {}
    for mode in ("retrieved", "oracle"):
        subset = [r for r in scored if r["mode"] == mode]
        total = sum(r["required"] for r in subset)
        modes[mode] = {"observations": len(subset), "literal_fact_coverage":
                       sum(r["covered"] for r in subset) / total if total else None}
    return {"passed": not errors, "failures": sorted(set(errors)), "modes": modes, "rows": scored,
            "usage_measured": all(isinstance(r.get("usage"), dict) and bool(r["usage"]) for r in observations),
            "outcomes": dict(Counter(r.get("outcome", "not_run") for r in observations))}
