# 阶段内 Agent 与 RAG

## 开启与兼容

本次实现保留 17 个业务节点及四类审批，在固定阶段内部运行经校验的里程碑 DAG。模型可插入补充检索、调整未完成里程碑，但不能删除业务前置条件、重写已完成节点或自行批准操作。

1. 备份目标数据库，使用迁移账号执行 `python -m alembic upgrade head`，再按现有部署流程授予应用账号新增表的 DML 权限。
2. 在 API 和 Worker 使用一致的环境变量：`AGENT_ENGINE=v2`。默认值为 `legacy`，已有 Run 从保存的 `engine_version` 选择引擎，不随配置改变。
3. 本地离线模式可以继续使用 SQLite、`MODEL_MODE=deterministic` 和 `EXECUTION_MODE=inline`。生产长任务使用 PostgreSQL、Celery Worker 和 Beat；API 和 Worker 必须运行同一代码版本。
4. 真实生成模型使用 `MODEL_MODE=real`、`MODEL_BASE_URL`、`MODEL_NAME`、`MODEL_API_KEY`，协议为 `/responses` 的结构化 JSON 输出。外部或私有服务必须支持该协议。

普通 Compose 使用 `docker compose --profile agent up --build` 启动新增 Worker/Beat；长任务设置 `EXECUTION_MODE=worker`。预生产 Compose 已有 Worker/Beat，更新共享的 `.env.preproduction.runtime` 后重启相应服务。只启动 API 而未运行 Worker/Beat 时，真实向量索引会停留在等待状态。

灰度先在隔离环境与少量新项目验证。关闭新 Run 灰度只需恢复 `AGENT_ENGINE=legacy`；已经创建的 v2 Run 仍需要新版 Worker，不能直接部署不认识新状态的旧代码。

## 执行、审批与恢复

阶段依次为调研方案、配置方案、配置执行与导入准备、导入培训验收、交付。每个阶段保存计划版本、必需验收条件、依赖、已完成里程碑、轮次、反馈与预算。

Generator 只能提出当前里程碑的动作，批次最多 3 个；业务工具每轮只允许一次，补充知识检索可有最多 3 个查询。参数使用封闭结构，未知工具及额外参数在执行前拒绝。每个动作重新校验原执行人的公司、项目和工具权限。成员导入额外要求 `import.execute`，请为执行人配置实施顾问角色。

工具动作、业务状态与数据库内效果在同一事务提交；失败工具通过 savepoint 回滚。Worker 每轮提交，Outbox 重投递从业务状态继续。inline 以请求事务提交，崩溃时整个未提交请求回滚；适合演示，不提供 Worker 的逐轮提交可见性。LangGraph 检查点不替代业务数据库中的进度。

每个里程碑默认最多重试 2 次，每阶段最多重规划 2 次、运行 20 轮。失败依次进入 retry、replan、blocked；权限问题、预算不足、非法规划直接 blocked。缺少 CSV 仍进入原有 `preparing_materials` 状态。v2 上线检查不满足时保存报告并 blocked；拒绝审批仍终止本次 Run。

`POST /api/runs/{id}/resume` 要求 `Idempotency-Key`、`expected_version` 和至少 3 字的 `reason`，调用者需有 `run.retry`，原执行人仍需有执行权限。恢复保留已完成动作，刷新本阶段重试额度，累计模型预算不清零。状态不明的 started 动作禁止自动恢复。材料修改需取消当前 Run 后新建 Run 重新审批；阶段内 replan 不允许重新生成已经审批完成的材料。

`GET /api/runs/{id}/actions` 提供完整动作记录；现有 steps 保存评估事件，Run 状态和 SSE 暴露当前计划、最近观察、知识引用、经验来源和预算。恢复事件记录人工处理说明。

模型预算采用每次请求 UTF-8 字节数加最大输出 token 的保守预留，并覆盖嵌套业务生成与重试；不是实际 token 账单。达到上限后需要人工检查并调整 `AGENT_TOKEN_BUDGET`。上下文优先保留原始需求和硬约束，再裁剪较早观察、经验和排名较低的知识。Embedding 与 reranker 费用需要由所选服务商监控。

## 知识索引与模型适配

CPU 方案及部署步骤见 [CPU 本地检索](RAG_CPU.md)。本地模型为 BGE-small-zh-v1.5 与 BGE-reranker-base，默认 FP32、CPU。文本按标题、段落切块，目标 220 tokens、重叠 30 tokens；本地 embedding 使用实际 tokenizer，mock/HTTP 模式使用近似词项长度。保留来源版本、标题、段落位置和引用 ID，常规 Markdown 表格重复列头。

PostgreSQL 使用原生 `vector(512)`、HNSW 和 GIN 全文索引，真实模式关键词使用 OpenSearch BM25。要求 pgvector >= 0.8，向量查询启用 iterative scan。关键词和向量分别最多召回 30 条，经 RRF 合并保留 16 条，重排总窗口最多 32 个，正文精确去重后返回最多 5 条。向量最低余弦相似度默认 0.35，必须通过实际资料校准。RRF 分数不是置信概率。SQLite 仅运行词项通道，不模拟向量准确率。

`EMBEDDING_BASE_URL` 与 `EMBEDDING_API_KEY` 独立于生成模型，不回退到生成服务。模型需返回 512 个有限且非全零的数值；旧部署需执行 `0020_bge_cpu` 迁移并重建索引。不能只改环境变量或截断旧向量。

模型、服务地址或 `KNOWLEDGE_INDEX_VERSION` 变化会得到新的索引身份；不同身份不混查。已存在正文由 `implementation.knowledge` Beat 任务异步处理；上传在真实向量模式下只排队。每份资料最多自动尝试 3 次，错误记录只保留类型，管理界面支持重建。离线 inline 无模型时即时切块，便于本地使用。

重建入口为 `POST /api/knowledge/{id}/reindex`，沿用公司管理员权限。最新版本即使仍在索引或已停用，也不会回退到旧版本。旧 Run 的证据快照保留审计用途；新的检索不使用已停用资料。

真实模式必须重排，使用本地 CrossEncoder 或独立 `/rerank` 接口。HTTP 输入 `{model, query, documents}`，返回 `results` 中覆盖全部输入的唯一 `index` 排列。输入包含标题和章节，服务错误显式返回，不静默伪造重排成功。

## 经验与安全边界

产品证据与经验分别存储。经验默认仅当前项目可见，最多召回 3 条；检索按工具匹配，不使用跨项目或跨租户经验。Reflector 首版只允许超时、依赖不可用、结构化输出失败三类规范化建议，不保存异常原文、凭据或客户材料。同 Run 中相同里程碑与业务输入随后成功，才标记为已验证。未验证经验不进入 ContextBuilder。

新增表启用 tenant RLS，经验额外由服务校验项目范围；模型不能用经验 ID 代替知识引用。模型输出仍经过现有需求一致性与证据 ID 校验，配置、导入、验收沿用材料绑定审批凭证。首版没有增加 shell、浏览器或任意外部工具。

## 验证与发布门槛

运行 `pytest -q`、`ruff check backend tests`、`mypy backend`、前端类型检查及构建。`tests/check_agent_postgres.py` 只接受本机独立 `agent_rag_test` 数据库，检查真实迁移、向量列、RLS、版本过滤和 Worker 恢复；Embedding 使用固定测试向量，不等同于真实模型质量验收。

`evaluations/rag_v2.jsonl` 提供 54 条固定标注候选，覆盖中文同义表达、字段名、错误标识和无答案；版本冲突由集成测试覆盖。`backend.rag_evaluation.evaluate_retrieval` 对比新旧 Recall@5、引用 precision、无答案准确率和耗时。**这些标签由本次实现编写，全部标记 pending_human，尚未达到“至少 50 条人工确认标注”的发布条件。** 应由领域人员复核后改为 human_verified，再使用实际模型跑评估与费用测试。

新引擎可在离线/隔离环境使用；生产默认关闭，真实模型、真实语料、人工标签与故障演练完成后再逐步开启。

本次 SQLite 词项通道基线：54 条候选中有答案 48 条，Recall@5 为 44/48（91.7%），旧演示检索为 47/48（97.9%），6 条无答案均返回空。该结果说明分块与权限工程已经可测，但不能声称语义检索质量提升；应在真实向量与重排模型上复测，必要时改进中文分词和查询表达。
