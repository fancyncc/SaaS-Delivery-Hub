"""Deterministic question decomposition and evidence answerability signals.

This module deliberately does not turn semantic similarity into answer confidence.
It extracts a bounded set of language-independent concepts and checks whether a
candidate contains the requested attributes.  A future NLI implementation can
replace ``assess_candidate`` without changing retrieval or authorization.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

SUPPORT = "SUPPORT"
PARTIAL = "PARTIAL"
CONTRADICT = "CONTRADICT"
TOPIC_ONLY = "TOPIC_ONLY"


@dataclass(frozen=True)
class Concept:
    key: str
    kind: str
    aliases: tuple[str, ...]


# Generic implementation vocabulary, not customer or holdout identifiers.  The
# same aliases are used in both directions, so English queries can be grounded
# in Chinese sources and Chinese queries can be grounded in English sources.
CONCEPTS = (
    Concept("brand", "attribute", ("brand", "vendor", "manufacturer", "品牌", "厂商", "制造商")),
    Concept("region", "attribute", ("region", "zone", "location", "区域", "地域", "地区", "可用区")),
    Concept("format", "attribute", ("format", "algorithm", "digest", "checksum", "hash", "格式", "算法", "摘要", "校验和", "哈希")),
    Concept("acknowledgement", "attribute", ("acknowledgement", "acknowledgment", "acknowledge", "first response", "响应", "首次响应", "受理", "确认")),
    Concept("update", "attribute", ("status update", "update interval", "update", "进展更新", "状态更新", "更新频率", "更新")),
    Concept("workaround", "attribute", ("workaround", "mitigation", "temporary solution", "临时解决方案", "临时方案", "变通方案", "缓解措施")),
    Concept("resolution", "attribute", ("resolution time", "resolution", "resolve", "fix time", "解决时间", "修复时间", "恢复时间", "解决")),
    Concept("guarantee", "attribute", ("guarantee", "guaranteed", "commitment", "service commitment", "保证", "承诺", "担保")),
    Concept("deadline", "attribute", ("deadline", "target", "within", "time limit", "期限", "时限", "目标时间", "多久")),
    Concept("condition", "attribute", ("condition", "must be met", "before", "条件", "前提", "满足", "只有")),
    Concept("exception", "attribute", ("exception", "unless", "except", "not apply", "例外", "除非", "不适用")),
    Concept("field", "attribute", ("field", "attribute", "property", "字段", "属性")),
    Concept("limit", "attribute", ("limit", "quota", "maximum", "上限", "限额", "额度")),
    Concept("priority", "attribute", ("priority", "precedence", "order", "优先级", "优先", "顺序")),
    Concept("version", "scope", ("version", "release", "revision", "版本", "修订版")),
    Concept("role", "scope", ("role", "actor", "operator", "角色", "操作人", "主体")),
    Concept("security_key", "topic", ("security key", "hardware key", "安全密钥", "硬件密钥")),
    Concept("object_storage", "topic", ("object storage", "blob storage", "对象存储", "存储桶")),
    Concept("manifest", "topic", ("manifest", "inventory file", "清单", "清册")),
    Concept("migration", "topic", ("migration", "cutover", "迁移", "切换")),
    Concept("support", "topic", ("enhanced support", "premium support", "support", "强化支持", "增强支持", "技术支持")),
    Concept("access", "topic", ("access", "permission", "访问", "权限")),
)

_BY_KEY = {concept.key: concept for concept in CONCEPTS}
_VALUE_PATTERNS = {
    "brand": (r"(?:品牌|厂商|制造商)\s*(?:为|是|[:：])\s*\S+", r"\b(?:brand|vendor|manufacturer)\s*(?:is|:)?\s*\S+"),
    "region": (r"\b(?:us|eu|ap|cn)-[a-z]+-\d+\b", r"(?:华东|华北|华南|亚太|欧洲|北美)\s*\d*"),
    "format": (r"\b(?:sha-?\d+|md5|crc\d*|json|yaml|csv|parquet)\b",),
    "deadline": (r"\b\d+(?:\.\d+)?\s*(?:minutes?|hours?|days?|business days?)\b", r"\d+(?:\.\d+)?\s*(?:分钟|小时|天|工作日)"),
    "condition": (r"(?:同时满足|仅当|只有.+才|必须.+(?:后|前)|\b(?:only if|provided that|must.+before|must.+after)\b)",),
}
_QUESTION_STOPWORDS = {
    "a", "an", "and", "are", "be", "before", "can", "does", "for", "how", "in", "is",
    "it", "of", "or", "the", "to", "what", "when", "which", "who", "with", "是否", "什么",
    "哪个", "哪些", "如何", "要求", "使用", "规定", "需要",
}


def _contains(text: str, alias: str) -> bool:
    if re.search(r"[\u4e00-\u9fff]", alias):
        return alias in text
    plural = r"(?:s|es)?" if re.fullmatch(r"[A-Za-z]+", alias) else ""
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(alias)}{plural}(?![a-z0-9])", text, re.I))


def _covers_concept(text: str, key: str) -> bool:
    concept = _BY_KEY[key]
    return any(_contains(text, alias) for alias in concept.aliases) or any(
        re.search(pattern, text, re.I) for pattern in _VALUE_PATTERNS.get(key, ())
    )


def text_language(text: str) -> str:
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if chinese and latin and min(chinese, latin) >= max(chinese, latin) * 0.25:
        return "mixed"
    if chinese:
        return "zh"
    if latin:
        return "en"
    return "unknown"


def language_pair(question: str, source: str) -> str:
    query_language = question if question in {"zh", "en", "mixed", "unknown"} else text_language(question)
    source_language = text_language(source)
    if "unknown" in (query_language, source_language) or "mixed" in (query_language, source_language):
        return "mixed"
    return "same" if query_language == source_language else f"{query_language}_to_{source_language}"


def matched_concepts(text: str, *, kinds: set[str] | None = None) -> tuple[str, ...]:
    return tuple(
        concept.key
        for concept in CONCEPTS
        if (kinds is None or concept.kind in kinds)
        and any(_contains(text, alias) for alias in concept.aliases)
    )


def _topic_terms(question: str) -> tuple[str, ...]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", question.casefold())
    aliases = {
        alias.casefold()
        for concept in CONCEPTS
        for alias in concept.aliases
        if re.fullmatch(r"[A-Za-z ]+", alias)
    }
    return tuple(dict.fromkeys(word for word in words if word not in _QUESTION_STOPWORDS and word not in aliases))


@dataclass(frozen=True)
class QuestionFrame:
    language: str
    required_atoms: tuple[str, ...]
    topic_atoms: tuple[str, ...]
    scope_atoms: tuple[str, ...]
    topic_terms: tuple[str, ...]

    @property
    def gated(self) -> bool:
        return bool(self.required_atoms)


@lru_cache(maxsize=2048)
def decompose_question(question: str) -> QuestionFrame:
    attributes = matched_concepts(question, kinds={"attribute"})
    scopes = matched_concepts(question, kinds={"scope"})
    return QuestionFrame(
        language=text_language(question),
        required_atoms=tuple(dict.fromkeys((*attributes, *scopes))),
        topic_atoms=matched_concepts(question, kinds={"topic"}),
        scope_atoms=scopes,
        topic_terms=_topic_terms(question),
    )


@lru_cache(maxsize=2048)
def bilingual_expansion(question: str) -> str:
    """Return a bounded mixed-language query while preserving the original text."""
    keys = matched_concepts(question)
    additions = []
    original = question.casefold()
    for key in keys:
        for alias in _BY_KEY[key].aliases:
            if alias.casefold() not in original:
                additions.append(alias)
    return question + (" " + " ".join(dict.fromkeys(additions)) if additions else "")


def assess_candidate(frame: QuestionFrame, hit: dict) -> dict:
    source = "\n".join(
        str(hit.get(key, ""))
        for key in ("title", "heading", "identity_context", "text")
        if hit.get(key)
    )
    source_body = "\n".join(
        str(hit.get(key, "")) for key in ("heading", "text") if hit.get(key)
    )
    covered = tuple(
        key for key in frame.required_atoms
        if _covers_concept(source, key)
    )
    missing = tuple(key for key in frame.required_atoms if key not in covered)
    topic_covered = tuple(
        key for key in frame.topic_atoms
        if _covers_concept(source, key)
    )
    scope_covered = tuple(
        key for key in frame.scope_atoms
        if _covers_concept(source, key)
    )
    literal_topics = tuple(term for term in frame.topic_terms if _contains(source, term))
    # Literal free-text topic terms are useful scoring signals but cannot be a
    # cross-language hard gate. Only mapped topic concepts are mandatory.
    topic_required = bool(frame.topic_atoms)
    topic_ok = bool(topic_covered or literal_topics) if topic_required else True
    if not frame.gated:
        label = SUPPORT
    elif not missing and topic_ok:
        label = SUPPORT
    elif covered and topic_ok:
        label = PARTIAL
    else:
        label = TOPIC_ONLY
    return {
        "label": label,
        "covered_atoms": list(covered),
        "missing_atoms": list(missing),
        "topic_atoms": list(topic_covered),
        "scope_atoms": list(scope_covered),
        "literal_topic_terms": list(literal_topics),
        "attribute_coverage": len(covered) / len(frame.required_atoms)
        if frame.required_atoms
        else 1.0,
        "topic_coverage": len(topic_covered) / len(frame.topic_atoms)
        if frame.topic_atoms
        else 1.0,
        "scope_coverage": len(scope_covered) / max(1, len(frame.scope_atoms))
        if frame.scope_atoms
        else 1.0,
        "language_pair": language_pair(frame.language, source_body),
        "gated": frame.gated,
    }


def coverage_matrix(frame: QuestionFrame, candidates: list[dict]) -> dict:
    matrix = {
        atom: [
            hit["id"] for hit in candidates
            if atom in hit.get("answerability", {}).get("covered_atoms", ())
        ]
        for atom in frame.required_atoms
    }
    missing = [atom for atom, sources in matrix.items() if not sources]
    return {
        "required_atoms": list(frame.required_atoms),
        "coverage": matrix,
        "missing_atoms": missing,
        "complete": not missing,
    }


def evidence_score(features: dict) -> float:
    """A diagnostic ranking score; answerability gates remain independent."""
    rerank = min(1.0, max(0.0, features["rerank"]))
    score = (
        0.45 * rerank
        + 0.25 * features.get("attribute_coverage", 0.0)
        + 0.15 * features.get("topic_coverage", 0.0)
        + 0.10 * features.get("query_terms", 0.0)
        + 0.03 * features.get("language_bridge", 0.0)
        + 0.02 * features.get("scope_coverage", 0.0)
    )
    if features.get("topic_only"):
        score -= 0.60
    return max(0.0, min(1.0, score))
