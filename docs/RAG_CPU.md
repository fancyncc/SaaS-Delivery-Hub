# CPU 本地检索

企业知识采用 `BAAI/bge-small-zh-v1.5`（512 维）和 `BAAI/bge-reranker-base`，设备为 CPU，基线使用 FP32。回答生成模型单独配置。本次不默认启用 INT8；量化前后必须在相同真实资料上比较排序和延迟。

## 参数与行为

| 参数 | 默认值 |
| --- | --- |
| EMBEDDING_LOCAL_MODEL | BAAI/bge-small-zh-v1.5 |
| EMBEDDING_DEVICE / RERANKER_DEVICE | cpu |
| EMBEDDING_DIMENSIONS / EMBEDDING_MAX_LENGTH | 512 / 512 |
| EMBEDDING_QUERY_STYLE | prefix |
| EMBEDDING_QUERY_INSTRUCTION | 为这个句子生成表示以用于检索相关文章： |
| EMBEDDING_BATCH_SIZE | 16 |
| RERANKER_LOCAL_MODEL | BAAI/bge-reranker-base |
| RERANKER_BATCH_SIZE / RERANKER_MAX_LENGTH | 4 / 256 |
| RERANKER_MAX_CANDIDATES / RERANKER_MAX_WINDOWS | 16 / 32 |
| RETRIEVAL_CPU_THREADS | 4，按实际核心数调整并为 API 和数据库留余量 |
| KNOWLEDGE_CHUNK_TOKENS / KNOWLEDGE_CHUNK_OVERLAP | 220 / 30 |
| KNOWLEDGE_INDEX_VERSION | chunks-bge-cpu-v2 |

查询保持原文并追加维护好的 SaaS 同义词；SSO 不会匹配 password 等词的子串。关键词与向量各召回最多 30 条，沿用租户、项目、有效版本过滤，RRF 合并后重排最多 16 条。重排输入为原始问题与标题、章节、正文。每个候选先分配一个窗口，再轮流追加窗口，总计最多 32 个；单个候选取已计算窗口的最高分。未覆盖的长文尾部可能漏检，因此优先使用短分块。去掉正文完全重复的片段，返回最多 5 条，保留原始引用 ID。

本地 embedding 模式使用真实 tokenizer 的字符偏移切块，保留原文；常规 Markdown 表格重复列头，长行受 token 预算约束，极宽表头退回普通分块。mock 与纯 HTTP embedding 模式继续使用近似词项长度切块，不声称精确 tokenizer 计数。私有会话附件仍使用原有授权范围内的词项检索；本次没有把它们迁入企业向量索引。

向量相似度下限沿用 0.35，必须用实际资料校准；没有加入固定 0.7 的 reranker 阈值。重排分数不解释为概率。来源权重和 MMR 未默认加入，避免未经评测改变证据排序。

## 安装与模型准备

从项目根目录使用虚拟环境（以下为 Windows PowerShell）：

```powershell
.venv/Scripts/python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv/Scripts/python.exe -m pip install -e '.[rag-local]'
# 将 config/rag.local.env.example 合并到自己的部署环境文件，保留数据库、密钥及生成模型配置。
$env:SAAS_ENV_FILE='.env.rag-cpu'
.venv/Scripts/python.exe scripts/download_rag_models.py
.venv/Scripts/python.exe scripts/check_rag_config.py
```

下载脚本是显式联网步骤。运行时仅从本地加载，不隐式下载、不回退其他模型。默认缓存为项目绝对路径 `knowledge/models`，可用 `RETRIEVAL_MODEL_CACHE` 覆盖；上线时将两个 `*_REVISION` 固定为已验收的模型提交版本。模型缓存不提交 Git。

真实检索需要 PostgreSQL/pgvector >= 0.8、OpenSearch、Redis 和已有 Celery Worker/Beat。SQLite 仍仅支持 mock。上传及重建只入队，Worker 分批编码，在全部向量与 BM25 发布成功后标记 ready；每批释放推理锁，让问答有机会执行。单进程内 embedding 与 reranker 串行推理。

## 迁移与重建

1. 停止旧版 API 和索引 Worker，按现有运维流程备份数据库。使用具有跨租户迁移权限的数据库账号运行 `python -m alembic upgrade head`；本项目 Alembic 读取 `DATABASE_URL`。
2. `0020_bge_cpu` 将向量列改为 `vector(512)` 并重建 HNSW。旧向量是派生数据，迁移置空，不把 1024 维截断为 512 维。原文、历史片段 ID 和证据快照保留。
3. 所有文档标记 pending，失败计数清零。新程序部署后启动索引 Worker/Beat，活动文档自动重建；待新索引 ready 前不返回旧版本。旧 OpenSearch 条目无法通过新的 SQL 索引身份校验。
4. 管理员可通过既有 `POST /api/knowledge/{id}/reindex` 重试失败文档。模型、前缀、长度或分块设置变化后也需要重建；这些配置包含在索引指纹中。

迁移 PostgreSQL 时使用 `row_security=off`，权限不足会失败，避免只迁移一部分租户。降级同样使派生索引失效，需要配合旧版模型重建；不会恢复旧向量。不要在新旧程序同时写索引时执行迁移。

## 独立 CPU 模型进程

需要让 API 和 Worker 共用一个常驻模型副本时，使用已有服务：

```powershell
# 此进程加载 local 配置，并分别设置 EMBEDDING_API_KEY、RERANKER_API_KEY。
.venv/Scripts/python.exe -m uvicorn backend.retrieval_server:app --host 127.0.0.1 --port 8010 --workers 1
```

API/Worker 使用 `config/rag.online.env.example`，两个 URL 指向该服务的 `/v1`，填写对应独立凭据和 BGE 模型名；online 在这里表示 HTTP 传输，权重仍在本机 CPU 上运行。调用方负责添加查询前缀，服务不会重复添加。此模式的建库使用上述近似分块。首次启动模型延迟和吞吐需实测；增加 Uvicorn worker 会复制模型并发占用 CPU 和内存。

## 验证

```powershell
$env:SAAS_ENV_FILE='config/rag.mock.env.example'
.venv/Scripts/python.exe -m pytest tests/test_retrieval_models.py tests/test_knowledge_v2.py tests/test_chat_scope.py -q
```

模拟模型测试覆盖 512 维校验、前缀、窗口预算、标题输入、异步入队、表头及索引指纹，不证明模型质量。`tests/check_agent_postgres.py` 仅允许本机独立 `agent_rag_test` 库，验证实际迁移、向量存储和 RLS；运行前设置 `AGENT_TEST_POSTGRES_URL`。真实模型使用 `evaluations/rag_v2.jsonl` 的 54 个候选问题，经业务人员复核后运行 `evaluate_retrieval`，记录 Recall@5、无答案准确率、均值与 P95 延迟。仓库测试不承诺 CPU 响应秒数。
