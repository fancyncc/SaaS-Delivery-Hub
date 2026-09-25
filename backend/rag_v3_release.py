"""Fail-closed, version-bound release and interpretable relevance model."""

import hashlib
import json
import math
import re
from pathlib import Path

from fastapi import HTTPException

from backend.config import get_settings

FEATURES = (
    "rerank",
    "gap",
    "identifier",
    "query_terms",
    "heading_terms",
    "structured",
    "phrase",
    "condition_terms",
)
RELEASE_FORMATS = ("md", "txt", "docx", "csv", "json", "pdf", "pptx", "xlsx")


def model_identity():
    from backend.rag_v3_runtime import settings

    s = settings()
    return {
        "embedding": s.embedding_local_model if s.embedding_mode == "local" else s.embedding_model,
        "embedding_revision": s.embedding_revision,
        "reranker": s.reranker_local_model if s.reranker_mode == "local" else s.reranker_model,
        "reranker_revision": s.reranker_revision,
        "embedding_dimensions": s.embedding_dimensions,
        "embedding_max_length": s.embedding_max_length,
        "query_style": s.embedding_query_style,
        "query_instruction": s.embedding_query_instruction,
        "reranker_max_length": s.reranker_max_length,
        "reranker_max_windows": s.reranker_max_windows,
        "feature_schema": 2,
        "parser_schema": 3,
    }


def release():
    try:
        config = json.loads(Path(get_settings().rag_v3_release).read_text(encoding="utf-8"))
        from backend.rag_v3_policy import PIPELINE_SCHEMA

        if config.get("pipeline_schema") != PIPELINE_SCHEMA:
            raise ValueError("证据流程版本变化，需要重新评测")
        identity = model_identity()
        if not all(
            re.fullmatch(r"[a-f0-9]{40}", identity[k])
            for k in ("embedding_revision", "reranker_revision")
        ):
            raise ValueError("模型权重尚未固定到提交版本")
        if config["identity"] != identity:
            raise ValueError("模型版本与校准不匹配")
        report_path = Path(config["report"])
        raw = report_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != config["report_sha256"]:
            raise ValueError("评测报告校验失败")
        report = json.loads(raw)
        if report.get("pipeline_schema") != PIPELINE_SCHEMA:
            raise ValueError("评测报告未覆盖当前证据流程")
        if not report["passed"] or not report["independent_review"]["approved"]:
            raise ValueError("锁定验证或独立复核未通过")
        from backend.rag_v3_evaluation import metric_failures

        if metric_failures(report["metrics"]) or any(
            metric_failures(report["per_format"][fmt])
            for fmt in RELEASE_FORMATS
        ):
            raise ValueError("验收指标未达硬性门槛")
        classifier_hash = hashlib.sha256(
            json.dumps(config["classifier"], sort_keys=True).encode()
        ).hexdigest()
        if report["classifier_sha256"] != classifier_hash or report["identity"] != identity:
            raise ValueError("验证报告与相关性分类器或模型版本不匹配")
        if report["question_count"] < 192 or set(report["formats"]) != set(RELEASE_FORMATS):
            raise ValueError("评测覆盖不足")
        if report["domain_count"] < 6 or not report["model_comparison_passed"]:
            raise ValueError("领域覆盖或模型对比未完成")
        if set(config["classifier"]["weights"]) != set(FEATURES):
            raise ValueError("相关性模型特征不匹配")
        values = [
            config["classifier"]["intercept"],
            config["classifier"]["threshold"],
            *config["classifier"]["weights"].values(),
        ]
        if not all(type(v) in (float, int) and math.isfinite(v) for v in values):
            raise ValueError("无效相关性模型")
        return config
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise HTTPException(503, "V3 尚未达到发布条件：" + str(exc)) from None


def features(question, hit, best):
    from backend.knowledge import lexemes
    from backend.rag_v3_index import exact_terms

    terms = set(lexemes(question).split())
    body = set(lexemes(hit["text"]).split())
    heading = set(lexemes(hit.get("heading", "")).split())
    ids = exact_terms(question)
    phrases = re.findall(r'[“"]([^”"\n]+)[”"]', question)
    conditions = re.findall(
        r"(?:不(?:得|能|允许)?|至少|至多|除非|仅|not|except|unless|only|before|after|\d+(?:\.\d+)?)",
        question,
        re.I,
    )
    score = hit["rerank_score"]
    return dict(
        zip(
            FEATURES,
            (
                score,
                score - best,
                sum(i in hit["text"] for i in ids) / max(1, len(ids)),
                len(terms & body) / max(1, len(terms)),
                len(terms & heading) / max(1, len(terms)),
                float(
                    hit.get("kind") in {"table_cell", "table_record", "table", "json_value", "code"}
                ),
                sum(p in hit["text"] for p in phrases) / max(1, len(phrases)),
                sum(c.lower() in hit["text"].lower() for c in conditions) / max(1, len(conditions)),
            ),
            strict=True,
        )
    )


def classify(features, model):
    logit = model["intercept"] + sum(features[k] * model["weights"][k] for k in FEATURES)
    value = 1 / (1 + math.exp(-max(-60, min(60, logit))))
    return value >= model["threshold"], value
