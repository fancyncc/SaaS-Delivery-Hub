"""Read-only readiness report; does not download models, call APIs or print secrets."""
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import get_settings


def report():
    s = get_settings()
    missing = []
    if s.rag_mode == "real":
        if s.embedding_dimensions != 512:
            missing.append("EMBEDDING_DIMENSIONS=512 (migration 0020 and reindex required)")
        if s.reranker_max_windows < s.reranker_max_candidates:
            missing.append("RERANKER_MAX_WINDOWS >= RERANKER_MAX_CANDIDATES")
        if not s.database_url.startswith("postgresql"):
            missing.append("PostgreSQL/pgvector")
        if not s.opensearch_url:
            missing.append("OPENSEARCH_URL")
        if s.model_mode != "real":
            missing.append("MODEL_MODE=real")
        for kind in ("embedding", "reranker"):
            if getattr(s, f"{kind}_mode") == "local":
                if importlib.util.find_spec("sentence_transformers") is None:
                    missing.append(f"{kind}: rag-local dependencies")
            elif not getattr(s, f"{kind}_base_url") or not getattr(s, f"{kind}_model"):
                missing.append(f"{kind}: URL/model")
        if s.llm_mode == "local":
            if not s.llm_local_model or not s.llm_local_base_url:
                missing.append("LLM_LOCAL_MODEL/LLM_LOCAL_BASE_URL")
        elif not s.model_name or not s.model_base_url or not s.model_api_key:
            missing.append("MODEL_NAME/MODEL_BASE_URL/MODEL_API_KEY")
    return {"mode": s.rag_mode, "ocr": "excluded", "configuration_complete": not missing,
            "services_available": None, "ready_for_real_rag": False,
            "missing": missing, "real_models_verified": False,
            "note": "Configuration check only; weights, services and retrieval quality have not been probed."}


if __name__ == "__main__":
    result = report()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["configuration_complete"] else 1)
