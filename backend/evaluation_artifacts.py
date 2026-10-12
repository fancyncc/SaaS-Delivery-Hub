"""Frozen inputs and append-only observations for offline acceptance runs."""

import hashlib
import importlib.metadata
import json
import os
import platform
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config/evaluation.json"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def read_rows(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(value, dict):
        value = value["rows"] if "rows" in value else [value]
    if not isinstance(value, list):
        raise ValueError("Expected a JSON report or JSONL observations")
    return value


def write_once(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical(value) + b"\n"
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def implementation_checksums(root=ROOT):
    # Include evaluator, parsers, authorization, Agent/memory code and test scenarios.
    paths = [p for folder in ("backend", "scripts", "tests", "alembic", "skills")
             for p in (root / folder).rglob("*") if p.suffix in {".py", ".ps1", ".md"}]
    paths.extend(p for p in (root / ".github/workflows").glob("*.yml"))
    paths.extend(p for p in (root / "pyproject.toml",) if p.is_file())
    return {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in sorted(paths)}


def environment():
    versions = {}
    for name in ("sqlalchemy", "httpx", "pypdf", "transformers", "torch", "pytest"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    profile = os.environ.get("EVALUATION_ENVIRONMENT_PROFILE")
    return {"python": platform.python_version(), "platform": platform.platform(),
            "machine": platform.machine(), "cpu_count": os.cpu_count(), "packages": versions,
            "target_profile_sha256": digest(Path(profile).read_bytes()) if profile else None}


def runtime_config(budget):
    from backend.config import get_settings
    from backend.retrieval_policy import PIPELINE_SCHEMA
    from backend.retrieval_release import model_identity

    s = get_settings()
    names = ("rag_mode", "retrieval_relevance_mode", "retrieval_min_rerank_score",
             "retrieval_same_language_min_evidence_score", "retrieval_en_to_zh_min_evidence_score",
             "retrieval_zh_to_en_min_evidence_score", "retrieval_mixed_language_min_evidence_score",
             "rag_pdf_enabled", "rag_office_enabled", "model_mode", "llm_mode", "model_name",
             "llm_local_model", "model_api_style", "model_max_output_tokens", "model_timeout")
    result = {name: getattr(s, name) for name in names}
    # Fingerprint endpoints without exporting potentially private URLs or credentials.
    result["service_endpoints_sha256"] = {name: digest(str(getattr(s, name)).encode()) for name in
        ("model_base_url", "llm_local_base_url", "embedding_base_url", "reranker_base_url", "database_url", "opensearch_url")}
    result.update(budget=budget, pipeline_schema=PIPELINE_SCHEMA, identity=model_identity())
    if s.retrieval_relevance_mode == "calibrated":
        config = json.loads(Path(s.retrieval_release).read_text(encoding="utf-8"))
        result["classifier_sha256"] = digest(json.dumps(config.get("classifier", config), sort_keys=True).encode())
    return result


def corpus_fingerprint(cases_path):
    path = Path(cases_path).resolve()
    cases = read_rows(path)
    if not cases or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Empty corpus or duplicate case IDs")
    sources = {}
    for case in cases:
        document = case["document"]
        source = (path.parent / document).resolve()
        if case["format"] != source.suffix.lower().lstrip("."):
            raise ValueError("Declared format differs from source file")
        if not source.is_relative_to(path.parent):
            raise ValueError("Source path must remain within corpus")
        sources[document] = digest(source.read_bytes())
    registry_path = path.with_name("sources.json")
    registry_hash = None
    if registry_path.exists():
        raw = registry_path.read_bytes()
        registry = json.loads(raw)
        registry_hash = digest(raw)
        if registry.get("cases_sha256", digest(path.read_bytes())) != digest(path.read_bytes()):
            raise ValueError("Frozen question checksum mismatch")
        registered = {s["document"]: s["sha256"] for s in registry["sources"]}
        if any(registered.get(name) != checksum for name, checksum in sources.items()):
            raise ValueError("Source registry checksum mismatch")
    return {"cases_sha256": digest(path.read_bytes()), "sources_sha256": sources,
            "registry_sha256": registry_hash, "case_ids": [c["id"] for c in cases],
            "validation_previously_exposed": registry.get("validation_previously_exposed") if registry_hash else None}


def freeze(cases_path, config, policy_path=POLICY, *, review_path=None):
    review = Path(review_path).read_bytes() if review_path else None
    return {"schema": 1, "created_at": datetime.now(UTC).isoformat(),
            "corpus": corpus_fingerprint(cases_path), "config": config,
            "policy": json.loads(Path(policy_path).read_bytes()),
            "policy_sha256": digest(Path(policy_path).read_bytes()),
            "review_sha256": digest(review) if review else None,
            "review": json.loads(review) if review else None,
            "implementation_sha256": implementation_checksums(), "environment": environment()}


def verify_frozen(frozen, cases_path, *, config=None, policy_path=POLICY):
    errors = []
    if frozen.get("corpus") != corpus_fingerprint(cases_path):
        errors.append("corpus_changed")
    if frozen.get("implementation_sha256") != implementation_checksums():
        errors.append("implementation_changed")
    if frozen.get("policy_sha256") != digest(Path(policy_path).read_bytes()):
        errors.append("scoring_policy_changed")
    if config is not None and frozen.get("config") != config:
        errors.append("runtime_config_changed")
    if config is not None and frozen.get("environment") != environment():
        errors.append("target_environment_changed")
    if errors:
        raise ValueError(",".join(errors))


class RunJournal:
    """Reserve the final path before any side effect; persist each result immediately."""

    def __init__(self, output, frozen):
        self.output = Path(output)
        if self.output.exists():
            raise FileExistsError(self.output)
        self.directory = self.output.with_suffix(self.output.suffix + ".run")
        self.directory.mkdir(parents=True, exist_ok=False)
        self.run_id = uuid4().hex
        self.frozen = frozen
        self.rows = []
        self.started_at = datetime.now(UTC).isoformat()
        write_once(self.directory / "frozen.json", frozen)
        write_once(self.directory / "run.json", {"run_id": self.run_id, "started_at": self.started_at})

    def append(self, row):
        if any(r["id"] == row["id"] for r in self.rows):
            raise ValueError("Duplicate observation: " + row["id"])
        with (self.directory / "observations.jsonl").open("ab") as stream:
            stream.write(canonical(row) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.rows.append(row)

    def finish(self, status, *, cleanup, errors=(), **extra):
        recorded = {r["id"] for r in self.rows}
        missing = [c for c in self.frozen["corpus"]["case_ids"] if c not in recorded]
        report = {"schema": 1, "run_id": self.run_id, "started_at": self.started_at,
                  "finished_at": datetime.now(UTC).isoformat(), "status": status,
                  "complete": not missing and status == "succeeded", "missing_case_ids": missing,
                  "test_data_cleaned": cleanup, "errors": list(errors),
                  "frozen": self.frozen, "frozen_sha256": digest(canonical(self.frozen)),
                  "rows_sha256": digest(canonical(self.rows)), "rows": self.rows,
                  "release_approved": False, **extra}
        if "source_snapshot" in report:
            report["source_snapshot_sha256"] = digest(canonical(report["source_snapshot"]))
        write_once(self.output, report)
        return report
