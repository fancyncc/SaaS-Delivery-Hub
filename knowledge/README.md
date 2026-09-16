# Knowledge corpus and local models

The original Chinese demo corpus in `backend/rag.py` supports mock operation. Real enterprise retrieval uses PostgreSQL/pgvector (512 dimensions), OpenSearch BM25 and RRF, followed by BGE reranking. Source documents remain in the database with tenant, project and version boundaries.

The CPU profile uses `BAAI/bge-small-zh-v1.5` and `BAAI/bge-reranker-base`. Model weights are explicitly downloaded to `knowledge/models` by `scripts/download_rag_models.py`; runtime loading is offline only. The cache is excluded from Git and Docker build contexts. See [CPU setup and migration](../docs/RAG_CPU.md).
