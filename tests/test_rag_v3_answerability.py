from backend.rag_v3 import classify_relevance
from backend.rag_v3_answerability import (
    PARTIAL,
    SUPPORT,
    TOPIC_ONLY,
    assess_candidate,
    bilingual_expansion,
    coverage_matrix,
    decompose_question,
)


def hit(identifier, text, heading=""):
    return {"id": identifier, "title": "Policy", "heading": heading, "text": text}


def rules():
    return {
        "relevance_mode": "rules",
        "min_rerank_score": 0.5,
        "same_language_min_evidence_score": 0.5,
        "en_to_zh_min_evidence_score": 0.35,
        "zh_to_en_min_evidence_score": 0.35,
        "mixed_language_min_evidence_score": 0.45,
    }


def test_bilingual_expansion_preserves_original_and_adds_both_languages():
    english = "Which checksum format is required in the migration manifest?"
    expanded = bilingual_expansion(english)
    assert expanded.startswith(english)
    assert all(term in expanded for term in ("校验和", "格式", "迁移", "清单"))

    chinese = "备份必须使用哪个区域？"
    expanded = bilingual_expansion(chinese)
    assert expanded.startswith(chinese) and "region" in expanded


def test_topic_only_candidate_cannot_answer_a_requested_attribute():
    question = "灾备数据库要求使用哪个厂商的产品？"
    assessment = assess_candidate(
        decompose_question(question),
        hit("generic", "灾备数据库每天执行备份，并保留三十天。"),
    )
    assert assessment["label"] == TOPIC_ONLY
    assert assessment["covered_atoms"] == [] and "brand" in assessment["missing_atoms"]


def test_cross_language_candidate_can_support_an_explicit_attribute():
    question = "Which region stores the backup archives?"
    assessment = assess_candidate(
        decompose_question(question),
        hit("region", "备份归档保存在华东 2 区域。", "备份存储"),
    )
    assert assessment["label"] == SUPPORT
    assert assessment["language_pair"] == "en_to_zh"
    assert assessment["covered_atoms"] == ["region"]


def test_atomic_coverage_can_be_completed_across_multiple_evidence_spans():
    frame = decompose_question("What are the acknowledgement and workaround targets?")
    first = hit("ack", "首次响应时间为 20 分钟。")
    second = hit("workaround", "临时解决方案应在 4 小时内提供。")
    for candidate in (first, second):
        candidate["answerability"] = assess_candidate(frame, candidate)
        assert candidate["answerability"]["label"] == PARTIAL
    matrix = coverage_matrix(frame, [first, second])
    assert matrix["complete"] and not matrix["missing_atoms"]
    assert set(matrix["coverage"]) == {"acknowledgement", "workaround", "deadline"}


def test_rules_reject_high_similarity_topic_only_and_keep_low_cross_language_support():
    topic_only = {
        "rerank": 0.95,
        "answerability_gated": 1.0,
        "attribute_coverage": 0.0,
        "topic_coverage": 1.0,
        "scope_coverage": 1.0,
        "query_terms": 0.8,
        "language_bridge": 0.0,
        "en_to_zh": 0.0,
        "zh_to_en": 0.0,
        "topic_only": 1.0,
    }
    assert classify_relevance(topic_only, rules())[0] is False

    supported = {
        **topic_only,
        "rerank": 0.05,
        "attribute_coverage": 1.0,
        "language_bridge": 1.0,
        "en_to_zh": 1.0,
        "topic_only": 0.0,
    }
    accepted, score = classify_relevance(supported, rules())
    assert accepted and score >= rules()["en_to_zh_min_evidence_score"]
