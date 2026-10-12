# Agent、聊天与工具

实施执行由固定业务工作流和阶段内 Agent 协作；聊天、记忆、Tools 与 Skills 复用授权边界。模型提出动作，代码校验权限、审批、幂等、预算和执行状态。

## 文档导航

[agent.md](agent.md) · [api.md](api.md) · [architecture.md](architecture.md) · [data_pipeline.md](data_pipeline.md) · [deployment.md](deployment.md) · [evaluation_report.md](evaluation_report.md) · [rag.md](rag.md) · [testing.md](testing.md)

## 本文内容

- [实施 Agent、审批与恢复](#implementation-agent)
- [AI 助手与对话](#ai-chat)
- [聊天工具链、流式输出与 MCP](#chat-agent)
- [上下文、历史与记忆](#context-memory)
- [Tools 与 Skills 运行时](#tool-skill-runtime)
- [平台助手产品操作手册](#platform-manual)
- [Tools 与 Skills 设计及实施记录](#tool-skill-plan)



<a id="implementation-agent"></a>

## 实施 Agent、审批与恢复

### 部署与兼容

本次实现保留 17 个业务节点及四类审批，在固定阶段内部运行经校验的里程碑 DAG。模型可插入补充检索、调整未完成里程碑，但不能删除业务前置条件、重写已完成节点或自行批准操作。

1. 备份目标数据库，使用迁移账号执行 `python -m alembic upgrade head`，再按现有部署流程授予应用账号新增表的 DML 权限。
2. API 与 Worker 使用同一套代码和配置。所有 Run 使用同一个实施 Agent；不再提供引擎版本选择。历史 Run 按已完成业务节点继续执行，不重放已提交操作。
3. 本地离线模式可以继续使用 SQLite、`MODEL_MODE=deterministic` 和 `EXECUTION_MODE=inline`。生产长任务使用 PostgreSQL、Celery Worker 和 Beat；API 和 Worker 必须运行同一代码版本。
4. 真实生成模型使用 `MODEL_MODE=real`、`MODEL_BASE_URL`、`MODEL_NAME`、`MODEL_API_KEY`，协议为 `/responses` 的结构化 JSON 输出。外部或私有服务必须支持该协议。

普通 Compose 使用 `docker compose --profile agent up --build` 启动新增 Worker/Beat；长任务设置 `EXECUTION_MODE=worker`。预生产 Compose 已有 Worker/Beat，更新共享的 `.env.preproduction.runtime` 后重启相应服务。只启动 API 而未运行 Worker/Beat 时，真实向量索引会停留在等待状态。

部署前在隔离环境验证迁移、旧任务续跑及恢复。执行模式只区分 inline 与 worker；生成模式只区分确定性逻辑与真实模型。它们是运行方式，不是不同项目版本。

### 执行、审批与恢复

阶段依次为调研方案、配置方案、配置执行与导入准备、导入培训验收、交付。每个阶段保存计划版本、必需验收条件、依赖、已完成里程碑、轮次、反馈与预算。

Generator 只能提出当前里程碑的动作，批次最多 3 个；业务工具每轮只允许一次，补充知识检索可有最多 3 个查询。参数使用封闭结构，未知工具及额外参数在执行前拒绝。每个动作重新校验原执行人的公司、项目和工具权限。成员导入额外要求 `import.execute`，请为执行人配置实施顾问角色。

工具动作、业务状态与数据库内效果在同一事务提交；失败工具通过 savepoint 回滚。Worker 每轮提交，Outbox 重投递从业务状态继续。inline 以请求事务提交，崩溃时整个未提交请求回滚；适合演示，不提供 Worker 的逐轮提交可见性。LangGraph 检查点不替代业务数据库中的进度。

每个里程碑默认最多重试 2 次，每阶段最多重规划 2 次、运行 20 轮。失败依次进入 retry、replan、blocked；权限问题、预算不足、非法规划直接 blocked。缺少 CSV 仍进入原有 `preparing_materials` 状态。上线检查不满足时保存报告并 blocked；拒绝审批仍终止本次 Run。

`POST /api/runs/{id}/resume` 要求 `Idempotency-Key`、`expected_version` 和至少 3 字的 `reason`，调用者需有 `run.retry`，原执行人仍需有执行权限。恢复保留已完成动作，刷新本阶段重试额度，累计模型预算不清零。状态不明的 started 动作禁止自动恢复。材料修改需取消当前 Run 后新建 Run 重新审批；阶段内 replan 不允许重新生成已经审批完成的材料。

`GET /api/runs/{id}/actions` 提供完整动作记录；现有 steps 保存评估事件，Run 状态和 SSE 暴露当前计划、最近观察、知识引用、经验来源和预算。恢复事件记录人工处理说明。

模型预算采用每次请求 UTF-8 字节数加最大输出 token 的保守预留，并覆盖嵌套业务生成与重试；不是实际 token 账单。达到上限后需要人工检查并调整 `AGENT_TOKEN_BUDGET`。上下文优先保留原始需求和硬约束，再裁剪较早观察、经验和排名较低的知识。Embedding 与 reranker 费用需要由所选服务商监控。

### 知识索引与模型适配

CPU 方案及部署步骤见 [CPU 本地检索](deployment.md#cpu-retrieval)。企业知识、项目资料和聊天使用统一知识检索管线：原件解析与结构化切分 → 授权范围过滤 → 向量与 BM25 召回 → RRF 融合 → 重排 → 证据覆盖检查与组装 → 引用输出。离线模式只使用可重复的词项检索，不代表真实模型质量。

真实模型、维度、重排器和门槛由同一套配置决定。常用 CPU 配置为 BGE-small-zh-v1.5 与 BGE-reranker-base；多语言配置是模型参数示例。修改模型或维度后须重建索引，不能截断旧向量。RRF 是排序融合算法，不是模型或置信概率。

`RETRIEVAL_INDEXING_ENABLED` 控制异步解析索引；`RETRIEVAL_RELEVANCE_MODE` 支持 rules 或 calibrated，校准模式要求已通过复核的发布材料。旧环境变量通过兼容别名读取。迁移 `0032_unified_knowledge_index` 将原索引表更名并保留原件、结构、向量和状态。历史执行快照保持不可变。

重建入口统一为 `POST /api/knowledge/{id}/reindex`；项目资料要求项目提交权限，公司通用资料要求公司管理员权限。历史二进制资料缺失原件时必须重新上传，不能从正文伪造原件。最新版本尚未就绪或已停用时不回退到旧版本。

### 经验与安全边界

产品证据与经验分别存储。经验默认仅当前项目可见，最多召回 3 条；检索按工具匹配，不使用跨项目或跨租户经验。Reflector 首版只允许超时、依赖不可用、结构化输出失败三类规范化建议，不保存异常原文、凭据或客户材料。同 Run 中相同里程碑与业务输入随后成功，才标记为已验证。未验证经验不进入 ContextBuilder。

新增表启用 tenant RLS，经验额外由服务校验项目范围；模型不能用经验 ID 代替知识引用。模型输出仍经过现有需求一致性与证据 ID 校验，配置、导入、验收沿用材料绑定审批凭证。首版没有增加 shell、浏览器或任意外部工具。

### 验证与发布门槛

运行 `pytest -q`、`ruff check backend tests`、`mypy backend`、前端类型检查及构建。`tests/check_agent_postgres.py` 只接受本机独立 `agent_rag_test` 数据库，检查真实迁移、向量列、RLS、版本过滤和 Worker 恢复；Embedding 使用固定测试向量，不等同于真实模型质量验收。

`evaluations/retrieval_baseline.jsonl` 提供 54 条固定标注候选，覆盖中文同义表达、字段名、错误标识和无答案；版本冲突由集成测试覆盖。`backend.rag_evaluation.evaluate_retrieval` 对比新旧 Recall@5、引用 precision、无答案准确率和耗时。**这些标签由本次实现编写，全部标记 pending_human，尚未达到“至少 50 条人工确认标注”的发布条件。** 应由领域人员复核后改为 human_verified，再使用实际模型跑评估与费用测试。

统一 Agent 可在离线/隔离环境使用；真实生成与真实检索需完成相应环境验收后启用。

本次 SQLite 词项通道基线：54 条候选中有答案 48 条，Recall@5 为 44/48（91.7%），旧演示检索为 47/48（97.9%），6 条无答案均返回空。该结果说明分块与权限工程已经可测，但不能声称语义检索质量提升；应在真实向量与重排模型上复测，必要时改进中文分词和查询表达。

<a id="ai-chat"></a>

## AI 助手与对话

平台专属助手已加入：内置 18 类平台功能知识，每轮注入明确的产品身份与依据优先级；涉及当前项目时按权限只读查询最新状态；接口问题从运行中应用的 OpenAPI 提取字段约束。客户制度不能覆盖平台能力，计划目标不能替代实时执行结果。详细维护方式见 [平台助手知识说明](agent.md#platform-manual)。

客户工作台顶部的「AI 助手」进入 `/app/chat`。每个会话属于当前用户和空间，其他成员不能读取。可选绑定一个有权访问的项目，绑定后不可切换，需新建会话。

### 问答与资料

- 支持一般 LLM 问答，并检索当前空间的企业知识、会话附件以及绑定项目的需求、实施材料和有权访问的最新各类交付物。
- TXT、MD、CSV、JSON 使用 UTF-8；DOCX 提取正文及表格段落；PDF 提取文本层。每份最多 2 MB / 15 万字符，最多 10 份附件，PDF 最多 100 页。不支持扫描件 OCR、旧 DOC、图片、嵌入对象或电子表格文件。
- 上传文件不进入共享知识库，只保存解析正文于私有会话。附件和项目文档按词项分块检索；企业知识复用现有混合检索（SQLite 词项，配置后的 PostgreSQL 可用向量和重排）。不能将附件词项检索称为语义向量检索。
- 回答提供编号来源与原文片段。概览采用有限片段，不能保证已阅读整份长文档。无匹配资料时明确说明，真实模型仍可回答一般知识问题。
- 未开启真实模型时页面明确显示离线检索，只展示匹配原文，不模拟模型回答。

### 上下文与记忆

- 每个会话最多 200 轮，历史保存在数据库，刷新后从侧边栏重新打开。
- 每轮携带最近 8 轮用户提问、仍有本轮证据支持的历史回答、初始提问和最多 3 条相关旧提问。检索查询结合最近两轮提问，支持追问。不会无限累计整个历史；这是有界历史选择，不是自动生成摘要。
- 历史回答引用的证据若本轮无法重新取到，该回答不会送回模型，避免从历史恢复已移除资料。历史页面仍保留当时的回答与证据快照；彻底移除会话记录请删除对话。
- 「管理长期记忆」主动保存偏好或背景，最多 4000 字符，在当前用户、当前空间的多个会话复用。可编辑、清空，或取消勾选本轮使用。不会自动推断或写入长期记忆。
- 项目访问每次检查，项目删除或权限撤销后不能读取关联会话。跨公司协作不会自动开放对方公司的知识库。

### 配置与部署

安装更新后的 Python 依赖（新增 `pypdf`），构建前端并迁移到 `0017_chat`。Windows 现有 `start.ps1` 会在启动时备份 SQLite 并迁移。正在运行的服务需要重启。

真实模型复用 `MODEL_MODE=real`、`MODEL_BASE_URL`、`MODEL_NAME`、`MODEL_API_KEY`，要求已有 `/responses` 结构化输出协议。配置不完整或调用失败会明确报错，本次问答不写入历史，不自动降级为假回答。聊天与实施执行共用授权知识检索，保留各自的入口。

PostgreSQL 新表启用 tenant RLS；迁移后按原部署流程授予应用账号新表 DML 权限。用户私有隔离由 API 同时校验 `tenant_id` 与 `user_id`。会话更新采用版本比较，问答通过请求 UUID 防止重试重复保存；并发冲突不会覆盖另一窗口的结果。

首版为请求完成后显示整条回答，暂不提供逐字流式输出、自动记忆提炼、跨空间记忆、联网搜索或业务写操作。资料和记忆会发送给配置的模型服务。

### 验证

`tests/test_chat.py` 覆盖上传、检索、概览、持久化、幂等与版本冲突、私有隔离、项目删除、平台拒绝、模型上下文/记忆、虚构引用拒绝、服务失败不落库和 DOCX/PDF 解析。

浏览器验收使用 `RUN_BROWSER_TESTS=1` 执行 `tests/test_chat_browser.py`，在独立测试数据库和端口 18114 验证上传、回答来源、记忆保存、刷新恢复以及手机布局。真实模型与 PostgreSQL 生产运行需要在相应环境另外验收。

2026-09-11 本地验证：全量回归 100 passed / 4 skipped；补充后的聊天专项 8 passed；聊天浏览器验收通过。Ruff、mypy、Vue 类型检查、前端构建通过。隔离 SQLite 空库升级至 0017、回退至 0016 后再升级以及 Alembic check 通过。当前本地使用离线模式，未配置真实模型，以上不代表真实模型质量或 PostgreSQL 环境验收。

<a id="chat-agent"></a>

## 聊天工具链、流式输出与 MCP

### 链路

网页通过 POST `/api/chat/conversations/{id}/messages`，携带 `stream: true` 接收 SSE：

1. 判断问题：明确的问题保留原文；指代模糊的问题由结构化模型决定保留、重写或澄清。重写仅用于检索，原始问题和项目编号保留。
2. 工具执行：Chat Completions 模式使用模型原生 `tools` / `tool_calls` / `tool` 消息协议，最多两轮、每轮一个只读检索工具。第二轮仅补充缺失信息。
3. 观察：工具执行器固定当前用户、租户、对话和项目范围；每次重新授权。统一客户来源走 BM25 与 BGE 向量召回、RRF 融合、BGE 重排、去重；平台手册及实时状态单独作为对应类型证据。
4. 回答：真实模型 token 增量直接发送到前端。完成标记、长度及引用编号校验成功后进行版本条件写入；失败输出 `error`，不保存半截答案。重试相同 request_id 返回已保存的 `done`。

SSE 事件包括 `step`、`delta`、`done`、`error`。`step` 是实际执行状态与决策摘要，不是模型内部思维链。简单问题不需要强制多轮循环；澄清问题不进行无意义工具调用。当前工具均只读，业务实施仍由原有审批工作流执行。

Responses 模式支持真实正文流式输出，但当前原生工具循环仅适配 Chat Completions；Responses 使用同一授权检索工具的确定性调度。保留 `stream: false` 的原 JSON 接口兼容旧客户端。

### MCP

`/api/mcp` 提供固定版本 `2025-11-25`、无状态 Streamable HTTP JSON 响应，支持 initialize、ping、tools/list、tools/call。GET/DELETE 返回 405。工具为 `search_authorized_context`，参数只有 conversation_id、query。内嵌 Agent 使用与 MCP 相同的工具分发器，避免经网络回传用户会话凭据。

当前认证沿用本产品客户会话 Cookie 与 `X-CSRF-Token`；客户端需要发送 `Accept: application/json, text/event-stream`，初始化后发送 `MCP-Protocol-Version: 2025-11-25`。这不是公开匿名 MCP 服务，也尚未提供第三方 OAuth 接入或外部 MCP 服务目录。平台账号不能读取客户聊天。权限在工具执行时检查，不依赖只读提示字段。

协议参考：https://modelcontextprotocol.io/specification/2025-11-25/basic/transports

### 本地部署

`docker-compose.local-rag.yml` 给 OpenSearch 和 BGE 服务添加仅回环地址的端口。先下载权重，再启动模型服务；模型服务要求随机访问密钥，API 的 embedding/reranker 密钥须与之保持一致。

```powershell
docker compose --env-file .env -f docker-compose.yml -f docker-compose.local-rag.yml --profile model-setup run --rm model-download
docker compose --env-file .env -f docker-compose.yml -f docker-compose.local-rag.yml --profile rag up -d postgres redis opensearch retrieval
.venv\Scripts\python.exe -m backend.local
```

本地配置需要 RAG_MODE=real、PostgreSQL/pgvector、OpenSearch，以及本地模型服务地址。EMBEDDING_MODE=online 表示 API 经 HTTP 访问本机 BGE 容器，模型计算仍在本机 CPU 完成。`LOCAL_INDEXER_ENABLED=true` 启用单进程索引调度；生产多进程部署使用原有 Celery indexer/beat，关闭本地调度。

OpenSearch 和检索模型 HTTP 客户端直接连接所配置的依赖地址，不继承系统代理。生成模型保持独立配置：本次青云示例为 `MODEL_BASE_URL=https://top.qingyuntop.ai/v1`、`MODEL_NAME=qwen-plus`、`MODEL_API_STYLE=chat_completions`，对应 POST `/v1/chat/completions` 和 Bearer 请求头。不要把 `/pricing` 网页地址填入模型 API 配置。

SQLite 迁移用 `scripts/migrate_local_rag.py`：先创建独立 PostgreSQL 数据库并运行 Alembic，再设置 SAAS_ENV_FILE 指向该库的私有 owner 配置。脚本创建 SQLite 备份，拒绝目标已有业务数据，事务内复制并核对所有来源主键；检索索引不复制，必须回填并重建。运行 API 使用非超级用户的数据库角色。

知识文档 知识检索 已支持扫描 PDF 页的 OCR、PPTX 文字和 XLSX 单元格；聊天附件仍只解析带文字层的 PDF。后续范围包括复杂版面与表格恢复、多查询检索质量评估、证据蕴含校验、召回阈值标定、长文整体总结与引用句级准确率。当前编号校验不等于语义事实已被自动证明。

### 切换宿主机与 Docker 启动

两种启动方式必须指向同一数据库：`.env` 中 `POSTGRES_DB` 必须等于宿主机 `DATABASE_URL` 的数据库名。Compose 会自行组装容器数据库 URL，并不会直接沿用宿主机的 `DATABASE_URL`。数据库名不一致会导致同一账号在一侧存在、另一侧登录返回 401。

容器默认通过 `retrieval:8010` 和 `opensearch:9200` 访问依赖；宿主机则使用 `127.0.0.1` 的映射端口。需要容器访问外部检索服务时，单独设置 `DOCKER_EMBEDDING_BASE_URL`、`DOCKER_RERANKER_BASE_URL` 和 `DOCKER_OPENSEARCH_URL`，避免宿主机地址污染容器配置。

### 青云生成模型验收

依据[服务商地址文档](https://qingyuntopai.apifox.cn/doc-9285041)，使用 `https://top.qingyuntop.ai/v1`，通过 Bearer 认证调用 `/chat/completions`。`qwen-plus` 的公开模型目录标为国产模型分组；令牌所属站点、允许分组和模型权限须在服务商控制台核对。不能仅凭公开价格页判断某一令牌已获调用权限。

保存 `.env` 后，已有容器不会自动更新环境变量，须重新创建 API 容器。以下检查读取容器实际配置，依次验证普通回答、原生工具调用和应用使用的流式解析器。测试会产生少量真实调用费用，首个失败即停止，不打印密钥或模型正文：

```powershell
docker compose --profile rag up -d --no-deps --force-recreate api
Get-Content -Raw scripts/verify_model_api.py | docker compose exec -T api python -
```

只有输出 `ready: true` 才表示上述生成能力全部通过；配置完整、容器健康或检索成功不等于生成模型验收通过。`401 / Invalid token` 表示当前网关拒绝认证，先核对令牌所属站点和网关；保留诊断中的请求编号供服务商定位。不要通过更改模型名、增加重试或返回模拟答案掩盖认证失败，也不要把密钥发送到未经确认的备用域名。

<a id="context-memory"></a>

## 上下文、历史与记忆

本版本按 P0/P1/P2 独立开关提供统一 token 预算、结构化状态和滚动摘要，以及私有历史混合召回、分作用域记忆、需确认的记忆候选、受验证的 Agent 经验和评分影子模式。默认关闭新策略，先验收 P0，再逐阶段启用。所有新表与兼容数据转换由迁移 0026、0027 管理。


### 数据流和信任边界

1. 用户发消息时校验所有权、空间与项目权限。旧会话按需入维护队列，即使本次输入超预算，已有记录仍能进行后台压缩。
2. 按 token 预算选取本轮证据、有效会话状态、近期原文、摘要、用户记忆和可验证的助手回答。所有最终生成请求再次进行完整请求预算校验。
3. 回答及引用校验成功后，将稳定消息 ID 和维护任务在同一事务提交。失败回答不进入历史。
4. 后台每次处理最多 8 轮已提交记录，生成独立快照，不修改对话版本号、不写长期记忆、不改变业务状态。
5. 下一轮补入快照尚未覆盖的原文；必要内容无法容纳时返回 422，不静默跳过中间轮次。

维护模型只读取用户原文和先前派生状态，不读取助手回答、文档或工具结果。状态分为 `goals`、`entities`、`constraints`、`decisions`、`open_questions`、`next_steps`；每项保留用户原文、消息 ID、轮次和替代关系。状态条目的文本必须逐字匹配原文。含糊或未明确确认的决定进入待澄清项。明确的“某事项改为／改成”才允许替代同事项旧记录；其他更正保守保留为待澄清，不能靠模型推断删除旧约束。

摘要始终标记为用户表述的派生信息，不能证明项目事实。离线模式只摘取原文，并标记 `extractive`；不具备真实模型的指代分析和语义摘要能力。真实模式使用结构化输出，检查来源 ID 和摘要 token 上限。助手历史答案仅在非空引用全部与当前证据一致时才可作为低优先级历史加入，不能成为业务证据。

附件变更使快照整体失效；源消息摘要值、用户、空间和生成版本不匹配也会失效。知识库版本和实时状态不会写入快照，因此始终由本轮检索和授权查询提供。会话或项目访问被撤销后，快照读取与任务执行均重新拒绝访问。删除会话会清理其任务和快照。

### 配置与启用

先执行数据库迁移 `python -m alembic upgrade head`，使用最小权限 PostgreSQL 应用角色时按现有部署流程重新授予新增表 DML 权限。新增迁移为 `0026_chat_context`、`0027_scoped_memory`，兼容 SQLite 与 PostgreSQL；不修改生产数据库配置或自动对生产运行迁移。

| 配置 | 默认值 | 含义 |
|---|---|---|
| `CHAT_CONTEXT_MODE` | `off` | `off` 旧历史组装；`shadow` 维护并测量新上下文但仍使用旧组装；`on` 使用新上下文 |
| `CHAT_CONTEXT_LOCAL_WORKER` | `false` | 本地 API 进程启动独立维护循环，每 5 秒扫描 |
| `CHAT_CONTEXT_TOKENS` | `12000` | 聊天模型完整输入的应用上限 |
| `AGENT_CONTEXT_TOKENS` | `12000` | 实施 Agent 输入上限，单位修正为 token |
| `AGENT_TOKEN_BUDGET` | `120000` | Agent Run 的累计实际消耗加未结算预留 |
| `CHAT_SUMMARY_TOKENS` | `1200` | 单份滚动摘要上限 |
| `MODEL_CONTEXT_WINDOW` | `32768` | 实际生成模型的上下文窗口，需按部署模型设置 |
| `MODEL_MAX_OUTPUT_TOKENS` | `4096` | 生成输出预留上限；工具规划最多 1024 |
| `CONTEXT_SAFETY_RATIO` | `0.1` | 模型窗口的安全余量比例 |
| `MODEL_TOKENIZER` | 空 | 显式指定 `tiktoken:<encoding>` 或 `hf:<本地模型目录>` |
| `MODEL_TOKENIZER_MAP` | `{}` | JSON 格式的模型名／网关别名到 tokenizer 映射，优先于全局配置 |

输入上限为 `min(应用上限, 模型窗口 - 输出预留 - 安全余量)`。完整请求计数覆盖系统提示、消息、工具、输出 schema 及保守协议开销。上下文选取另留封装余量，最终请求仍独立校验；当前问题与硬约束不截断。

安装 tiktoken 支持：`python -m pip install -e ".[context]"`。按实际模型选择 encoding，并在部署准备阶段预热其词表缓存。HF 路径使用本地 transformers tokenizer，禁止远程自定义代码；可使用已有 `rag-local` 可选依赖，但**不会把 BGE tokenizer 用于生成模型**。

未配置、缺依赖或 tokenizer 无法加载时采用明确标记的保守 Unicode token 估算，不使用 UTF-8 字节数充当 token。此估算可能较大，不能声称是实际用量。即使成功加载 tokenizer，协议封装仍是预估；只有服务商返回的 usage 才作为实测。修改或安装 tokenizer 后重启进程，以清除不可用配置缓存。

真实部署先使用 `shadow` 验证效果，再切到 `on`。本地启用 `CHAT_CONTEXT_LOCAL_WORKER=true`；生产保持其为 false，使用既有 Celery worker 和 beat，新定时任务名为 `implementation.chat_context`。不需要另建 Redis 队列，也不能只启动 beat 而没有 worker。

### 持久化、重试与接口

- `chat_context_snapshots`：用户／空间／对话归属、已处理轮次、摘要覆盖轮次、原文摘要值、附件摘要值、结构化状态、摘要、生成次数、生成版本和模式。
- `chat_context_tasks`：按对话合并任务，保存目标版本、状态、租约、重试次数和下次执行时间。处理前后都验证执行人的权限；租约过期可恢复，旧执行者不能覆盖新快照。
- 连续失败最多 5 次，退避上限 300 秒；失败不影响已保存回答。用户下一次提问会重新调度未完成的历史任务。
- 最近 8 轮为原文窗口；窗口外新增至少 8 轮或预组装 token 占用超过 80% 时更新摘要。
- `GET /api/chat/conversations/{id}/context` 返回 `state`、`summary`、`processed_turn`、`summary_until`、`generation`、`producer_version`、`mode`、`status`、`error_code` 和 `target_turn`。沿用对话所有权与项目权限检查，不返回内部推理。
- 发送消息与 SSE 事件格式兼容，历史消息新增稳定 `id`，保留原有 `request_id`。

关闭 `CHAT_CONTEXT_MODE` 可回到原有历史选择策略，保留快照、任务、消息及长期记忆；完整请求预算保护始终生效。回滚功能不执行降级迁移，也不删除新数据。

### 观测与验收

Prometheus 新增 `saas_context_tokens`（估算、预留、实际输入／输出及误差）、`saas_context_layer_tokens`、`saas_context_trimmed_total`、`saas_context_lag_turns`、`saas_context_jobs_total`。标签只有固定类别，不包含用户、项目、消息 ID 或原文。Agent 状态分别保留 `tokens_estimated`、`tokens_actual`、`tokens_unsettled` 和用于限额的 `tokens_reserved`，跨阶段及恢复保留。

回归命令：

```text
python -m pytest tests/test_context_budget.py tests/test_conversation_context.py tests/test_context_migration.py tests/test_chat.py tests/test_chat_streaming.py tests/test_chat_scope.py tests/test_chat_management.py tests/test_agent_loop.py tests/test_state_machine.py -q
```

`evaluations/context_cases.jsonl` 含 30 个固定长对话场景，覆盖 50／150 轮、六类约束、明确更正及主题切换。测试验证状态保持和来源检查；它们是确定性回归，不代表真实模型语义质量已达到 95%。真实模型上线前，仍需对实际模型配置跑人工标注的端到端问答评估，并验证 PostgreSQL 实例上的 RLS、并发和实际 tokenizer／usage 误差；SQL 生成测试不能替代 PostgreSQL 运行验收。


### P1：历史和分作用域记忆

设置 `CHAT_HISTORY_ENABLED=true` 后，已校验的消息切换为 `chat_messages` 中独立的 user/assistant 记录，保留请求 ID、时间、步骤、引用和回答模式。迁移 0027 先逐条回读比较再建立校验标记，保留原 JSON；切换后的会话停止写旧 JSON。关闭新历史召回后，已经切换的会话仍从独立记录读取和写入，以免丢失新消息。

对话详情及发送结果默认返回最近 50 轮和 `next_before`；`GET /api/chat/conversations/{id}/messages?before=...&limit=50` 向前翻页。默认最大 2,000 轮，未切换的 P0 对话保留 200 轮。前端提供加载旧消息入口；复制对话会读取所有页面。

历史召回仅在当前用户、当前空间、当前对话内进行，索引用户原文，以 BM25 + 生成配置独立的现有 embedding 服务进行 RRF 融合，最多返回 6 个片段及相邻用户原文。使用单独的 `conversation_history` 类别，不生成项目资料引用。向量服务不可用时返回词项召回并记录降级。后台按维护进度建立向量；无索引时词项检索仍可用。新历史、近期原文、摘要和状态根据消息 ID 去重。索引 generation 使用模型／revision 配置指纹，旧模型向量不混用。

设置 `CHAT_MEMORY_ITEMS_ENABLED=true` 后，使用私有 Memory Item。`user` 只允许显式通用偏好，跨空间生效；`workspace` 默认限制当前空间；`project` 和 `conversation` 进一步限制适用范围，均不共享给其他成员。相同 key 按 conversation > project > workspace > user 覆盖，过期或撤销条目不进入上下文。同范围重复 key 返回 409，不能自动覆盖。

`GET/POST /api/chat/memory-items`、`PATCH/DELETE /api/chat/memory-items/{id}` 提供管理接口；更新、删除需版本号。条目按相关度和预算逐项进入上下文，不再要求整段全部带入。旧记忆原样迁移为 workspace 的 `__legacy_workspace__` 条目，由 `/api/chat/memory` 继续维护；即使关闭新功能也保持兼容文本同步，不自动提升跨空间范围。前端长期记忆窗口提供条目管理和范围选择。

### P2：候选、经验与评分

`CHAT_MEMORY_CANDIDATES_ENABLED=true` 只是部署开关；用户还必须通过 `PUT /api/chat/memory-preferences` 显式启用 `auto_extract`。后台仅为“请记住… / 以后请… / 我的偏好是… / 我是…”这类明确的用户原文生成 workspace 候选，不对模型推断、助手回答或文档自动提取。候选有效期 30 天，按归一化原文去重；已拒绝的同一候选不重复提出。

`GET /api/chat/memory-candidates` 返回原文、来源消息及重复／冲突条目。`POST /api/chat/memory-candidates/{id}/accept|reject` 需要 `expected_version`；替换冲突条目还需明确的 `replace_id` 和 `replace_version`。来源失效、权限撤销、过期或并发变更时拒绝保存。候选状态和正式条目在同一事务提交；前端显示待替换内容，并使用“确认替换并保存”按钮，不静默覆盖。

`AGENT_EXPERIENCE_ENABLED=true` 扩展三类代码维护的经验模板：完整阶段的按序工具凭证验证 workflow；成功高风险工具加对应 approved 审批验证 constraint_pattern；成功文件校验加无错误且有行数据的 ImportJob 验证 validation_rule。每类都通过独立注册验证器读取持久化证据。未通过、缺失验证器或工作流版本不同的经验不召回。模板建议固定且不能变成可执行规则，历史审批永不替代当前审批。

`CONTEXT_SCORING_MODE=shadow` 记录与固定优先级选择的差异，不改变回答；验收后可设为 `on`。分数为相关度 40%、可信类别 30%、时效性 15%、任务价值 15%，同分优先较短块。必需块不参与淘汰，当前证据始终排在历史和建议之前。`saas_context_scoring_difference` 记录影子差异，`saas_history_recall_total` 记录混合召回和降级情况。

新增阶段回归：`python -m pytest tests/test_scoped_memory.py tests/test_context_budget.py -q`。包含 2,000 轮分页、消息／旧记忆迁移、关开关后数据保留、作用域覆盖、过期、来源删除、候选冲突与拒绝、历史语义融合／降级和验证经验场景。真实模型质量、线上 PostgreSQL 并发和服务商 token 误差仍需部署环境验收。

<a id="tool-skill-runtime"></a>

## Tools 与 Skills 运行时

实现日期：2026-10-06；验收记录更新于 2026-10-07。对应 [补充计划](agent.md#tool-skill-plan)。

### 已实现的入口

| 能力 | 入口 | 范围与结果 |
| --- | --- | --- |
| 项目材料检查 | 项目创建/编辑页；`POST /api/project-briefs/check`，请求 `{"draft": {...}}` | 客户登录；确定性检查必填值、格式、部门去重和明确人数冲突，返回缺失字段、问题、规范化草稿及可提交对象。未补齐时 `submission=null`。|
| 成员导入预览 | Run 材料准备页；`POST /api/projects/{id}/imports/preview`，请求 `{"csv_text":"..."}` | 需要 `import.validate`；复用正式校验的映射、部门、角色与目标冲突规则。返回预计新增/已存在、有效/错误行和冲突数；不创建工作区、成员、ImportJob 或审批。|
| 可选实施工具 | Agent 阶段规划；聊天原生 function calling；`POST /api/mcp` | 四个工具为 `check_project_brief`、`preview_import`、`search_official_docs`、`read_official_page`。聊天/MCP 只读业务工具需要绑定项目；导入工具传授权 `job_id`，不接受任意客户 CSV。|
| 受控实施方法 | `skills/` 与 `backend/skill_runtime.py` | 四个 Skill 按业务节点选择；Run 固化正文、参考文件、版本及哈希。不会执行 Skill 脚本，也不由模型决定额外权限。|
| 方法报告 | 实施交付物 | 配置评审、迁移准备、角色培训练习、验收对照。真实模式按 Schema 编制；离线模式使用固定提纲。报告明确标记建议，真实验收仍由业务代码判断。|

独立接口始终可做只读检查；`SKILL_RUNTIME_ENABLED` 控制运行时方法、附加报告，以及 Agent/聊天/MCP 的业务检查工具。它不改变原有正式业务接口。

实施 Agent 保留固定业务步骤与四个审批关口，在当前阶段白名单内选择可选工具。业务执行仍使用内置模拟 SaaS，外部 CRM 写入连接器不在本次交付范围。

### 配置与部署

保持默认关闭：

```dotenv
SKILL_RUNTIME_ENABLED=false
SKILL_MAX_TOKENS=8000
OFFICIAL_WEB_ENABLED=false
OFFICIAL_SEARCH_API_KEY=
OFFICIAL_PRODUCTS={}
OFFICIAL_WEB_TIMEOUT=15
OFFICIAL_WEB_MAX_BYTES=500000
```

启用实施方法时设置 `SKILL_RUNTIME_ENABLED=true`，重启 API 和 Worker，创建新的测试 Run。网页工具独立启用，需要 Brave Search API 凭据和管理员登记的公开产品目录。例如以下**占位示例必须换成实际产品的官方域名与公开主题**：

```dotenv
OFFICIAL_PRODUCTS='{"crm":{"name":"实际 CRM 产品名称","domains":["docs.vendor.example"],"topics":{"permissions":"user role permissions","import":"customer data import"},"versions":["v1"]}}'
```

模型输入仅传 `product`、`topic` 目录键和已登记版本。查询由服务器构造，不发送客户名称、需求正文、邮箱或 CSV。没有匹配的产品返回 `product_unmapped`；不能替模拟 SaaS 虚构厂商。

运行 `alembic upgrade head` 部署新版本，当前新增迁移是 `0031_conversation_tools`，给现有会话表加入工具额度和私有调用记录。先按现有部署流程备份，再迁移与重启；本次开发没有迁移生产数据库。Docker 镜像包含 `skills/`，主 Compose 给 API 与 Worker 传同一组配置，预生产从其 runtime env 文件读取。

修改 Skill 正文不改变已有 Run 固化内容。运行时开关、网页目录或网络限额变化会使已有 Run 的身份检查明确阻塞；恢复原配置后可继续，或创建新 Run 重新审批。关闭新增开关后，旧工具目录和原有聊天路径仍可用。

### 工具执行和证据

统一响应包含 `call_id`、`tool`、`version`、`status`、`result`、`sources` 和 `warnings`。工具成功完成检查不代表材料通过；查看 `result.complete` 或 `result.valid`。外部核验失败时明确返回业务结果 `unavailable`，不声称已取得官方证据。

官方搜索使用 Brave 固定接口，最多三个结果，去重后签署有效一小时的范围引用；正文工具只能读取该引用。引用绑定租户、用户、会话版本或 Run 阶段及官方目录策略，不能拿上一轮或上一阶段的搜索引用再次读取。已固化正文仍可作为后续分析证据。首版支持一个 Provider，尚无管理员选择其他搜索服务的界面。

网页读取只允许登记的 HTTPS 精确域名、443 端口和公开 DNS 地址。TCP 连接固定到已验证 IP，保留原域名 Host、TLS SNI 与证书校验；每次跳转重新检查，最多三次，总超时包含 DNS、跳转和读取。限制解压后大小及 HTML/TXT/JSON 类型；不运行 JavaScript，需要登录或没有静态正文时说明限制。

正文带 URL、标题、产品、内容哈希、获取时间与片段，单条文本最多 8000 字符。页面版本和发布时间首版不自动提取：`version_verified=false`、`published_at=null`；查询版本不能当作页面版本，获取时间不能当作发布时间。网页是事实候选，不能授权操作或证明本次部署通过验收。

Agent 每阶段材料检查/导入预览各最多两次、官方搜索一次、正文读取两次；恢复和重规划不重置额度，相同动作成功重放复用记录。网页正文进入追加式 Run 快照，并供后续 Agent 上下文和差距分析引用。内部 RAG 的 Embedding、RRF、Rerank 路径没有改变。

真实 `chat_completions` 聊天首轮强制内部查询，内部查询最多两次；未启用网页时总轮数为二，启用后为四。MCP 的可选工具额度按会话版本保存，每轮搜索一次、读取两次，业务检查各两次。先预约额度，再发网络请求；权限、归档、会话版本变化会丢弃结果。调用结果存于私有工具字段，不伪装成用户附件或聊天消息。其他模型协议目前仍执行一次服务端内部查询，可显式使用 MCP 调用新增工具。

### 测试与真实服务验收

```powershell
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/test_tool_skill_extension.py tests/test_tool_skill_integration.py -q -p no:cacheprovider
cd frontend
npm run typecheck
npm run build
```

浏览器测试在构建前端并安装 Chromium 后，以 `RUN_BROWSER_TESTS=1` 运行 `tests/test_browser_flow.py`，覆盖材料检查、只读预览及 80 人完整交付流程。Windows 已安装 Edge 时可设置 `PLAYWRIGHT_CHANNEL=msedge`，无需另装 Chromium。

真实服务探针不读项目数据、不改变业务记录；默认只检查配置和 Skill 文件，`--exercise` 允许一次小型模型调用、一次官方搜索和一次正文读取：

```powershell
.venv/Scripts/python.exe scripts/verify_tool_skill_services.py --exercise
```

报告保存在 `tests/artifacts/tool_skill_services.json`，不输出密钥。Schema 和引用校验不等同于语义质量评审；真实模型开关前后质量比较、真实搜索 Provider 及指定产品版本核验需要独立记录。自动化网络固定响应不计入真实服务通过项。

实施和验收进度见 [补充计划](agent.md#tool-skill-plan) 的交付记录。流程图见 [总览](../images_ref/overview.png)、[实施工作流](../images_ref/01_implementation_workflow.png)、[Agent 循环](../images_ref/02_agent_loop.png)、[聊天助手](../images_ref/03_chat_assistant.png)、[知识检索](../images_ref/04_knowledge_retrieval.png)、[Skill 运行时](../images_ref/05_skill_runtime.png) 和 [官方网页工具](../images_ref/06_official_web_tools.png)。

<a id="platform-manual"></a>

## 平台助手产品操作手册

知识版本：platform-assistant-2026-09-11.1。

2026-09-11 验证：125 项后端测试通过、4 项默认跳过；AI 对话浏览器专项单独通过；Ruff、mypy、Vue 类型检查与构建通过。当前本地服务已加载新版，实际验证了平台知识来源、XL-104 的等待材料状态及导入错误、接口字段约束。真实模型尚未配置，未将这些结果视为真实 LLM 综合回答质量验收。

助手使用五类依据：平台内置手册、当前运行版本 OpenAPI、授权实时状态、客户资料、会话上下文与主动保存的记忆。平台手册解释产品规则；实时状态解释当前执行情况；客户资料描述业务要求，不覆盖平台权限和实际能力。

实时查询仅检查当前账号可访问的未删除项目，最多 50 个候选及 6 个详情，明确返回是否截断，不代表全平台总数。绑定项目可直接查询不受候选列表排名影响。查询每次重新校验权限，并分别检查 run.view、approval.view、import.view、artifact.view。只暴露摘要、权限和导入错误，不读取密码、模型密钥或其他用户的聊天记忆。

接口约束由当前应用 OpenAPI 自动生成，保留字段类型、必填、范围等约束，排除示例和默认值。需要复杂嵌套细节时指向 /docs。

维护：功能变化时更新 backend/platform_knowledge.py 中相应条目并提升 VERSION，对照条目中的源文件核验；扩充 tests/test_platform_assistant.py 的问题集。手册不会自动读取整个仓库或所有私有文档，尚未记录的细节必须明确不足。该版本是领域上下文与受控只读查询，不是对模型微调。

真实模型未配置时仅显示检索证据与实时摘要；真实 LLM 的理解、综合回答与抗提示注入效果仍需在模型服务配置后评测。

### 平台功能总览与页面导航

本平台帮助企业完成 SaaS 上线实施：需求收集、产品知识检索、差距分析、实施计划、配置方案与执行、成员 CSV 导入、培训、上线验收和结项。支持多租户、公司协作、项目角色、四类审批、审计、实时进度、可恢复执行和 AI 对话。客户首页 /app 创建和查看项目；/app/workbench 查看待办与企业知识库；/app/chat 使用 AI 助手；/app/spaces 切换空间；/app/profile 管理个人资料；/app/company 公司设置；/app/company/member-imports 批量邀请公司成员。执行详情 /app/runs/{id}，Trace /app/runs/{id}/trace。平台人员单独从 /platform/login 登录，后台 /platform，评测 /platform/evaluations。说明入口不代表当前用户有权访问。

维护核对：backend/main.py,frontend/src/router.ts。

### 账号注册、邮箱、公司与空间

客户以账号和密码自助注册，账号为 3–40 字符，以字母开头，可含数字、下划线、点和短横线；注册时归一为小写。密码至少 10 字符并由后端校验。注册后得到个人空间，不需先创建企业；在个人主页绑定并验证邮箱后可创建公司。手机号可填写但暂不验证，不用于登录或找回密码。每个账号最多加入一家企业，同时保留自己的个人空间。空间与账号页面切换当前空间；会话、项目和知识范围随空间改变。忘记密码使用已绑定邮箱的重置流程；重置成功会撤销旧会话。不要向助手提交密码或一次性链接。

维护核对：backend/onboarding.py,backend/auth_routes.py。

### 权限体系与为什么不能操作

权限由账号类型、平台角色、公司身份、项目主角色及临时能力共同计算，并从数据库角色目录读取。公司管理员管理公司成员、项目、协作、回收站和审计，不自动拥有全部执行或审批能力。项目经理可启动、取消和重试执行；实施顾问可准备材料、校验与执行导入；审批人可审批；客户联系人参与验收；viewer 只读。每个项目成员只有一个主角色。临时能力带原因和有效期，不能绕过审批、删除或成员管理等敏感权限。看不到按钮先检查当前空间、项目成员关系、角色、执行状态及是否是审批发起人；真实权限以本轮实时快照为准，不能仅根据职位称呼推断。

维护核对：backend/permissions.py,backend/project_access_routes.py。

### 项目创建与实施文书字段

首页建立项目需要项目名称、客户公司、联系人、联系邮箱、实施人数、目标上线日期、至少一个部门和具体实施需求。还可填行业、顾问、迁移范围、验收条件、约束与风险。需求应描述谁在何场景做什么、权限边界和预期结果，不只填写模块名。日期采用 YYYY-MM-DD。创建项目保存 ProjectDocument，并为创建人建立项目经理关系。项目与执行 Run 状态独立；同一项目可以有历史执行，但不允许同时运行多个活动 Run。不要把公司业务规模、项目计划人数、已导入成员数和登录账号数混为一谈。

维护核对：backend/schemas.py,backend/main.py。

### 17 个实施节点与四类审批

17 节点按顺序为：建立执行上下文→收集需求→检索产品知识→差距分析→生成实施计划→计划审批→检查当前配置→生成配置变更→配置审批→应用配置→校验导入文件→导入审批→执行导入→生成培训材料→上线检查→验收审批→结项。四类审批是计划、配置、导入、上线验收。普通节点自动推进，审批时暂停；缺少成员 CSV 时进入 preparing_materials 等待顾问提交。通过审批不等于整个项目结束，需看下一节点及最新 Run。

维护核对：backend/workflow.py。

### 审批职责分离、材料绑定与版本冲突

企业高风险操作的发起人不能审批自己的请求，应由项目 approver 独立决定。审批绑定项目、Run、类型及具体材料内容；修改已审批材料后不能复用旧审批凭证。审批提交需要 expected_version，过期页面提交返回 409，需要刷新；未具备权限或自批通常返回 403。拒绝必须填写整改意见，当前 Run 失败、项目阻塞。验收报告 ready=false 时不能批准。个人空间所有者确认有独立规则，不能把个人空间行为套到企业审批。助手只能解释、查状态和指引操作，不会代替用户批准。

维护核对：backend/main.py,backend/delivery.py。

### 配置变更、回读和实际执行范围

配置方案列出原值、目标值、风险和原因，经批准才应用并保存前后快照、回读核对与审计。内置 SaaS 可持久化部门、固定业务角色、模板、任务状态、自定义字段名称与到期提醒配置。业务角色固定 admin、manager、member、viewer；它们不同于实施平台项目角色。字段名称存在不代表任意字段类型、动态表单或完整可自定义 RBAC 已实现。配置目标是内置模拟 SaaS；没有通用第三方 CRM/ERP/HR 自动连接器。调整配置方案需要沿用材料与审批版本校验。

维护核对：backend/delivery.py,backend/delivery_routes.py。

### 成员 CSV 导入格式与错误处理

目标业务成员 CSV 推荐 UTF-8，字段 name,email,department,role；中文姓名/邮箱/部门/角色等别名也可映射。必填值、邮箱格式、重复邮箱先校验，再核对目标部门和角色。姓名最长120、邮箱160、部门120、角色40字符。项目成员导入 CSV 请求最多 2,000,000 字节。错误行号包含表头，首条数据在第2行。未知部门或非法角色必须修正；已有邮箱但资料不同不静默覆盖。只有 preparing_materials 阶段可提交校验，合格后等待导入审批。导入写入目标模拟工作区，不等同于创建平台登录账号。失败行补偿需创建关联的新材料/执行并重新审批，成功成员不重复创建。

维护核对：backend/imports.py,backend/delivery.py,backend/main.py。

### 公司成员邀请与批量激活

公司管理员在公司设置邀请成员，或从 /app/company/member-imports 批量校验并提交员工名册。流程包含重复核验、部门和员工身份信息、激活链接，账号接受邀请后加入公司。账号最多属于一家企业。公司员工导入用于实施平台登录成员，与项目执行里的 SaaS 业务成员 CSV 是两条不同流程。邮件调试模式只生成预览链接，真实投递依赖 SMTP。不得把已生成激活链接说成邮件已送达。

维护核对：backend/member_import_routes.py,backend/admin_routes.py。

### 培训、交付物、反馈和验收

交付工作区展示材料、需求差距、实施计划与甘特图、配置、成员导入、反馈整改和交付文件。完成项目通常有实施计划、管理员指南、成员指南、FAQ、验收报告、实施总结六类版本交付物，可下载 Markdown 或 JSON。上线检查核对实际配置、人数、管理员、角色部门、导入及培训材料。报告检查通过仍需独立验收审批；未结项时可能没有实施总结。整改任务只因实际检查结果满足而关闭，不因提交反馈自动关闭。文件生成不代表真实学员已经培训或客户已现实签章。

维护核对：backend/delivery.py,backend/delivery_routes.py。

### 执行状态、取消、重试与恢复

Run 可能为 pending、running、preparing_materials、waiting_approval、blocked、succeeded、failed、cancelled。waiting_approval 等待批准；preparing_materials 等待材料；failed/cancelled 可按权限新建重试 Run，保留 retry_of_run_id。取消会同时取消待审批请求。v2 blocked 可经有 run.retry 权限者填写至少3字原因、带版本号恢复同一 Run，原执行人仍需执行权限。累计模型预算不清零，状态不明的已开始动作不能自动恢复。已完成项目不能简单再次启动同一执行。先检查最新状态和阻塞原因，不要直接改数据库状态。

维护核对：backend/state_machine.py,backend/main.py,backend/agent_loop.py。

### 长程 Agent、真实模型与执行模式

默认 legacy 流程使用确定性逻辑，v2 在固定业务阶段内加入里程碑依赖、工具动作、评估、重试、重规划、恢复与项目经验。AGENT_ENGINE=v2 影响新 Run；已有执行使用保存的引擎版本。真实生成模型需 MODEL_MODE=real、MODEL_BASE_URL、MODEL_NAME、MODEL_API_KEY，服务支持 /responses 结构化输出。模型接入存在不等于凭据已配置或质量已验收。聊天与工作流是不同入口，聊天不要求启用 v2。inline 适合本地演示；worker 依赖 Celery/Redis/Beat，逐轮提交。助手没有 shell、浏览器、任意 SQL 或自主业务审批权限。

维护核对：backend/config.py,backend/intelligence.py,backend/agent_loop.py。

### 企业知识版本、检索与索引

公司管理员选择公司通用或具体项目范围，录入标题、版本、模块、来源、授权和正文；正文20到30000字符。同标题版本必须递增并保持原项目范围。知识按标题和段落分块，中文双字词项、英文标识符检索；SQLite 使用词项，PostgreSQL 可配置1024维 pgvector、全文检索与排名融合，可选重排。资料更新、停用和索引身份变化影响召回；最新版本未就绪也不回退旧版。真实向量索引依赖 Worker/Beat，失败最多自动尝试3次，可管理员重建。公司客户资料不是平台官方功能定义，虚构演示制度不覆盖产品规则。

维护核对：backend/knowledge.py,backend/knowledge_routes.py。

### AI 助手附件、上下文与长期记忆

AI 助手 /app/chat 支持平台知识、当前授权状态、企业知识、绑定项目资料和私有附件。TXT/MD/CSV/JSON 为UTF-8；DOCX提取正文段落；PDF需文本层，最多100页，不支持扫描件OCR。每份附件最多2 MiB及15万字符，每个会话10份。会话最多200轮，最近8轮、初始提问和最多3条相关旧问题参与上下文；被引用资料无法在本轮重新获得时，不把旧回答作为当前证据。长期记忆由用户主动填写，最多4000字符，同一用户同一空间跨会话复用，可清空或停用，不自动提炼。会话私有；项目对话仅检索本项目和公司通用知识，当前空间对话可检索授权项目知识，切换范围须新建对话；删除附件后历史快照仍保留，删除会话可去除历史。离线展示规则化状态说明和最多两段短摘录，完整片段折叠在来源中，真实综合问答需要配置LLM。当前回答整条返回，暂不逐字流式输出。

维护核对：backend/chat.py,backend/chat_routes.py。

### 跨公司协作和只读支持访问

项目所属公司邀请另一家公司协作，受邀公司管理员接受后，其人员还需项目成员关系和角色。撤销协作立即影响访问，历史记录保留。平台支持访问需限定公司、项目、只读权限、原因及到期时间，由目标公司管理员审批。平台会话不能直接调用客户业务写接口。能解释支持功能，不等于当前客户助手可查看平台后台或其他公司的数据。

维护核对：backend/project_access_routes.py,backend/support_routes.py。

### 项目删除、回收站和审计

项目删除先进入公司回收站，默认保留30天，可按权限恢复；物理清理由清理任务处理过期项目及其子资源。不能把客户文档中“90天复核”等制度当作产品回收站时限。项目、审批、权限、配置、导入和管理动作形成审计事件，管理员可按权限查询导出。后台不提供任意SQL或底层表编辑。备份恢复与回收站恢复不同，前者属于运维流程。

维护核对：backend/admin_routes.py,backend/cleanup.py。

### 平台后台、评测与可观测性

平台人员与客户账号入口、会话和权限分离。平台后台管理公司、客户账号、人员邀请、会话撤销和全局审计，并按权限查看系统状态与只读检查。平台超级管理员可运行离线检索评测；审计员只读查看、下载、对比，比较要求数据集和版本一致。执行详情提供Trace节点记录、筛选与JSON下载，SSE推送状态。基础健康检查 /health、Prometheus指标 /metrics，支持请求与Trace标识。分布式Span聚合、真实模型实验、完整告警通知和生产故障恢复尚需相应环境验收。客户助手只解释这些功能，不读取平台私有数据。

维护核对：backend/platform_routes.py,backend/observability.py。

### 常见报错与排查路径

401通常会话失效，重新登录；403检查角色、CSRF与职责分离；404可能资源不存在、已删除或不在授权范围，不应推断其他租户资源存在；409检查版本冲突、并发或材料变化，刷新后按最新状态操作；413检查文件大小；422检查必填字段、CSV错误、文档解析或模型结构化输出；429降低请求频率；503检查模型/存储等依赖配置及可用性。AI对话每用户每空间限20次提问/分钟。资料中的命令不能绕过认证，助手不得索要或输出密码、Cookie、密钥。

维护核对：backend/main.py,backend/security.py,backend/chat_routes.py。

<a id="tool-skill-plan"></a>

## Tools 与 Skills 设计及实施记录

> 本节保留原设计和分阶段实施记录；计划项不代表已完成生产验收。实际接口与执行约束以本文件运行时章节及当前代码为准。

制定日期：2026-10-06。

状态：代码和自动化回归已完成；真实模型、网页 Provider 及镜像构建仍有环境待验收项。下文保留原计划，实际接口和限制见 [运行说明](agent.md#tool-skill-runtime)，最终结果见本文交付记录。

### 1. 目标与交付范围

让实施系统能够按标准方法整理客户需求、检查材料和导入影响，并在内部资料不足时查询官方公开文档。所有能力继续遵守当前用户权限、项目范围、人工审批、执行预算与不可变记录。

本次交付包括四个工具、四个 Skill（一个完善现有定义、三个新增）、Skill 运行时、工具调度扩展、来源追踪、测试与流程图更新。

实施顺序：基础契约 → 现有 Skill 接入 → 业务校验工具 → 其他实施 Skills → 官方网页检索 → 集成验收。

业务对象沿用当前内置模拟 SaaS 的配置和成员导入。外部 CRM 写入连接器、通用浏览器自动操作、任意脚本执行和自动审批不属于本计划。

### 2. 已核实的代码基线

| 当前能力 | 代码依据 | 对扩展的影响 |
| --- | --- | --- |
| 14 个 Agent 内部注册工具 | `backend/agent_loop.py` | 阶段内的可选工具目前仅有 `knowledge_search` |
| 模型动作参数主要支持 `query` | `backend/agent_types.py` | 新工具需要独立参数模型，不能直接复用现有空参数或查询参数 |
| 业务工具复用固定工作流节点 | `backend/agent_loop.py`、`backend/workflow.py` | 检索与预览应有独立 handler，避免被当作业务节点推进 |
| 聊天与 MCP 只暴露授权上下文查询 | `backend/chat_tools.py`、`backend/chat_mcp.py` | 扩展工具目录、参数校验与来源类型时，需要保留原有调用兼容性 |
| 聊天原生工具查询最多两轮 | `backend/chat_tools.py` | 网页搜索与正文读取增加调用步骤，需要明确总轮次和分项上限 |
| 已有项目文书 Skill | `skills/create-implementation-project-document/` | 完善并接入运行时；当前未发现应用内的 Skill 发现和加载逻辑 |
| 固化输入、审批材料与调用记录 | `backend/run_snapshots.py` | Skill 身份、网页证据和工具结果应复用现有记录机制 |
| 成员 CSV 校验会调用工作区初始化函数 | `backend/delivery.py` | 预览必须分离只读查询与初始化写入，不能直接给现有函数加只读标记 |

所有 Run 使用同一个实施 Agent，保留业务前置条件、阶段规划与评价循环，按节点加载 Skill，在阶段白名单内使用新增工具。

### 3. 工具交付契约

| 工具 | 输入与范围 | 输出 | 接入方式 |
| --- | --- | --- | --- |
| `check_project_brief` | 创建前的草稿字段，或服务端读取的项目固化材料；两种输入入口分别校验 | 完整性、缺失字段、冲突、待澄清问题、规范化建议及原文依据 | 项目创建前检查接口；实施调研阶段；聊天通过会话范围读取 |
| `preview_import` | 当前项目的 CSV 草稿，或其授权 ImportJob；限制大小和行数 | 字段映射、有效与错误行数、预计新增/已存在/冲突数量、错误明细、目标状态版本 | 材料准备页面与接口；Agent migration 阶段；聊天仅接受授权任务引用 |
| `search_official_docs` | 产品资料目录中的厂商/产品标识、公开问题、可选版本 | 官方结果标题、网址、摘要、匹配的产品/版本、时间信息与来源状态 | 聊天；Agent discovery/configuration 阶段，可选且默认关闭 |
| `read_official_page` | 本轮搜索得到的授权结果引用；服务端解析目标网址 | 清洗正文、最终网址、内容哈希、获取时间、页面标注的版本/日期、可引用片段 | 聊天与上述 Agent 阶段；复用同一官方来源策略 |

共同规则：

- 用户、租户、会话和 Run 身份由服务端注入；不由模型参数决定。
- 显式区分工具执行状态与业务结果。例如成功完成检查但发现缺失字段，返回 `status=succeeded` 与 `complete=false`，不能误触发工具重试。
- 使用严格参数模型与统一结果结构，包含工具版本、调用 ID、结果、来源、警告和可分类错误；原始异常不得携带密钥进入记录。
- 预览与检查提供建议；正式创建项目、配置变更和导入继续走现有业务 API、版本校验与审批。
- `preview_import` 不创建工作区、ImportJob、成员、审批或交付物。目标工作区尚未就绪时返回相应状态，不初始化资源；执行时重新校验，预览不是执行授权。
- 正式导入允许使用现有校验流程；公共字段和业务规则提取为共享函数，避免预览与实际执行规则分叉。

### 4. Skill 交付清单与加载方式

| Skill | 阶段 | 内容和输出 |
| --- | --- | --- |
| `create-implementation-project-document` | 创建前、调研 | 完善已有定义，形成实施草稿、缺失项及具体业务要求 |
| `review-configuration-proposal` | 配置 | 比对原配置与建议变更，形成角色/部门需求、差异、影响和风险清单 |
| `prepare-member-migration` | 迁移 | 使用预览结果整理字段映射、错误修正建议和导入准备报告 |
| `prepare-training-and-acceptance` | 培训、验收 | 根据角色与实际执行证据生成培训提纲、验收对照和待整改事项 |

首版 Skill 使用指令、参考资料、输出模板与 Schema，不直接运行 Skill 附带脚本。客户事实、执行结果和审批凭证由工具及业务代码提供。

Skill 运行时包括：

1. **发现**：读取受控 `skills/` 目录的名称、描述、阶段、版本、资源清单和内容哈希，校验名称重复与相对引用。
2. **选择**：按节点固定映射；Agent 由代码先按阶段筛选，再允许模型在候选集内建议选择。模型没有额外访问或工具授权能力。
3. **加载**：先传入简短元数据，仅加载本轮选中 Skill 的正文与必要参考文件；限制数量、大小和 Token，阻止越界路径和目录外引用。
4. **生成与检查**：Skill 定义编制方法，结构化模型生成草稿，确定性 Schema 与业务检查验证输出。缺失事实保留为未知，不编造联系人、人数、日期、产品能力或验收通过结论。
5. **留痕**：Run 启动时固定可用 Skill 版本/内容身份及对应内容；每次使用记录 Skill、资源、输入来源与结果。中断恢复继续使用固定内容，不能因工作区文件变更而替换方法。
6. **接口适配**：文书草稿中的“待补充”不能直接填入日期、人数等强类型 API 字段。草稿和正式提交模型分离；缺失值有明确标记，补齐后再生成 `ProjectCreate`。

权限、审批条件、强制业务依赖与完成状态由代码决定，Skill 不能覆盖这些约束。

### 5. 官方网页检索设计

#### 5.1 检索触发与来源

先查询现有授权资料。只有问题明确要求最新官方信息，或现有资料不足且该问题属于公开产品能力时，才触发官方检索。项目进度、成员信息与审批状态从项目数据获取。

来源由管理员维护厂商/产品标识、官方文档域名、语言与版本范围。当前模拟 SaaS 未匹配官方产品时返回 `product_unmapped`，不能替它搜索或虚构厂商资料。

搜索服务采用可替换 Provider 接口。开发与 CI 使用固定响应适配器；真实 Provider 在 P4 核对域名限定能力、结果结构、可用性和费用后确定，参数与密钥从部署配置读取。

#### 5.2 网络与数据边界

- 查询由公开产品名称、功能术语和版本构成；默认不发送客户名称、邮箱、员工名单、CSV、完整需求正文或内部文档。安全查询无法构造时停止外部检索。
- 校验搜索结果、每次跳转和最终目标的官方域名；限制 HTTPS、端口、响应大小、内容类型、超时和跳转次数。禁止访问本机、内网、链路本地与云元数据地址，并覆盖 IPv4/IPv6、DNS 和重定向场景。
- 读取公开静态 HTML/TXT；遇到登录、动态页面或不支持的文件格式返回明确限制。首版不执行页面 JavaScript。
- 网页内容进入现有不可信数据边界，只提供事实候选，不成为执行指令、工具授权或审批凭证。

#### 5.3 调用额度与证据

以下是拟议初始上限，配置校验后在验收时调整：每个聊天请求最多一次官方搜索、最多两次正文读取，每次搜索最多三个结果。聊天关闭外部检索时维持原有两轮；开启时总工具轮次上限为四，首轮仍为内部授权查询。

Agent 对外部搜索与正文读取设置每阶段的分项上限，并计入现有轮次、总预算与工具观察。重试和恢复不重置累计额度。搜索结果去重，内容不变时复用固定证据。

网页证据保存最终 URL、标题、厂商/产品、页面版本、发布时间（缺失则未知）、获取时间、内容哈希和引用片段。获取时间不等同于发布时间。外部证据与客户资料有独立来源类型及引用展示。

网页服务失败或证据不足时明确说明限制；不修改项目业务状态、不宣称验证了产品能力。已取得的内部资料仍可用于回答，并注明外部核验未完成。

### 6. 工程接入位置

| 模块 | 拟议工作 |
| --- | --- |
| `backend/tool_registry.py`、`backend/tool_types.py`（新增） | 严格参数、handler、阶段、权限、风险、版本、额度与工具结果契约；保持旧工具名兼容 |
| `backend/tools/`（新增） | 实现材料检查、导入预览、官方搜索和正文读取 handler；复用共享业务校验 |
| `backend/skill_runtime.py`（新增）、`skills/` | 元数据发现、阶段选择、按需加载、Schema、内容版本固定 |
| `backend/agent_types.py`、`backend/agent_loop.py` | 独立参数验证与分发；将硬编码的可选 `knowledge_search` 扩展成阶段白名单；区分可选只读工具和强制业务节点 |
| `backend/workflow.py`、`backend/intelligence.py` | 固定节点加载 Skill；记录来源与方法；保持人工审批和真实执行检查 |
| `backend/delivery.py`、`backend/imports.py` | 分离工作区初始化与只读校验，复用解析、映射、字段和目标冲突规则 |
| `backend/chat_tools.py`、`backend/chat_mcp.py` | 统一目录和 handler，注入会话范围，保留原参数；按功能开关增加网页工具及预算 |
| `backend/chat_context.py`、`backend/chat_stream.py` | 外部证据预算、引用编号、来源展示与失败事件；Skill 方法不能作为事实来源 |
| `backend/run_snapshots.py`、`backend/observability.py` | 工具/Skill 身份、使用内容、来源快照与调用 Trace；复用存储和追加式记录 |
| 前端项目材料、导入页面及聊天来源组件 | 材料缺失提示、只读预览、官方网页引用与限制说明 |
| `backend/config.py`、`.env.example` | 提供拟议开关 `SKILL_RUNTIME_ENABLED`、`OFFICIAL_WEB_ENABLED`、Provider、域名策略及调用额度；外部检索默认关闭 |
| `docs/`、`images_ref/` | 工具目录、Skill 编写与部署说明、验收报告及对应流程图 |

拟议材料入口为 `POST /api/project-briefs/check`（客户登录、仅检查请求草稿）及 `POST /api/projects/{project_id}/imports/preview`（项目导入校验权限、只读）。已有材料校验和正式导入接口继续兼容。

数据模型优先复用已有工具动作和执行快照。如果新字段或表确有必要，实施阶段增加顺序迁移及适用的租户隔离测试；不修改已发布迁移。旧 Run 保持旧身份和执行语义。

### 7. 阶段、交付物与门槛

时间为单人有效开发的相对估算，合计约 11–17 个工作日；真实 Provider 接入和模型服务可用性可能影响 P4/P5，P0–P3 可独立完成。

| 阶段 | 估算 | 交付物 | 完成门槛 |
| --- | --- | --- | --- |
| P0 契约与基线 | 1–2 日 | 工具/Skill 契约、阶段矩阵、功能开关、固定案例与基线报告 | 现有工具参数和引擎路径兼容；新增来源与动作结果可明确区分 |
| P1 现有 Skill 接入 | 2–3 日 | Skill 运行时、固化版本、草稿/提交模型分离、现有需求 Skill 的 实施 Agent 接入 | 明确缺失项、不编造事实；修改 Skill 文件后已有 Run 仍使用原内容 |
| P2 业务校验工具 | 2–3 日 | 材料检查、只读导入预览、页面/API 与 Agent 接入 | 预览没有业务写入；预览和执行使用一致规则；跨范围请求被拒绝 |
| P3 其他实施 Skills | 1–2 日 | 配置评审、迁移准备、培训与验收三个 Skill 及结构化报告 | 原四个审批关口完整；报告对应当前执行证据，模拟 SaaS 能力边界明确 |
| P4 官方网页工具 | 3–4 日 | Provider、来源策略、搜索与读取、聊天/Agent/MCP 接入、引用记录 | 网络边界、隐私查询、额度和错误场景通过；真实搜索/读取取得可复核证据 |
| P5 集成与交付 | 2–3 日 | 回归与质量比较、部署文档、关闭开关验证、更新流程图 | 统一 Agent、inline/worker、聊天与 MCP 通过相关回归；真实服务结果单列 |

每阶段先完成对应门槛再进入依赖阶段。网页配置或模型认证不可用时保留诊断和待验收项，不把固定响应测试算作真实服务验收。

### 8. 测试和质量验收

#### 8.1 必须通过的行为检查

- Skill 重复名、越界引用、资源过大、错误元数据；阶段选择、按需加载、Token 超限与固定版本恢复。
- 草稿缺失联系人/日期/人数；部门重复、事实矛盾、可选项为空；输出能明确区分未知值和客户事实。
- CSV 格式与字段映射、部门/角色不匹配、重复成员和目标冲突；目标工作区缺失；空文件、超限文件；预览前后业务表内容及记录数量一致。
- Agent 非法工具、参数、阶段与依赖；已有业务工具单次写入约束；检查不通过不等同于工具失败；重试/重规划/恢复后的额度和幂等记录。
- 官方网页搜索空结果、版本不匹配、没有发布时间、服务超时、正文失败、重定向、内网/DNS 地址、页面注入、超长正文和已撤销资料访问。
- 聊天首轮授权检索、总轮次、范围固定、引用编号、SSE 错误、请求幂等、功能关闭；MCP 工具目录和旧调用参数兼容。
- 固化材料审批、职责分离、版本冲突、租户隔离、inline/worker、中断恢复与跨 Worker Trace 的相关回归。

建议新增 `tests/test_tool_registry.py`、`tests/test_skill_runtime.py`、`tests/test_project_brief_tools.py`、`tests/test_import_preview.py`、`tests/test_official_web_tools.py`，并扩展现有 Agent、聊天、快照、Worker 和浏览器测试。

#### 8.2 固定场景集

至少准备 24 个核心案例：完整需求 6 个、缺失事实 6 个、配置/验收证据冲突 4 个、成员导入问题 4 个、官方文档空缺或版本问题 4 个。额外网络和授权故障独立测试。

对同一输入、模型和配置比较开关关闭/开启的结果：缺失项召回、错误事实数量、报告 Schema 合格率、引用正确性、人工修改项、Token、调用数和耗时。代码断言及权限检查全部通过；固定事实缺失项识别目标不低于 95%，输出的执行/审批结论必须全部有对应业务凭证，引用必须全部指向实际取得的来源。

真实模型另用固定案例验收；供应商搜索返回变化时保留原响应和证据，使验收可复核。未达目标时调整方法或工具策略，不能靠增加无界循环通过。

#### 8.3 最终交付证据

验收报告分别记录静态检查、自动化行为测试、浏览器流程、真实模型、真实网页 Provider、Worker 恢复与来源快照。报告列出已通过、跳过、失败、依赖未满足的项目及其证据路径。

### 9. 启用、兼容与回退

新增运行时先默认关闭，使用固定案例验证；之后在指定测试项目启用 Skill 与业务检查，最后单独启用官方网页工具。现有业务执行和四个审批关口保持有效。

关闭开关影响新的请求和 Run。已有 Run 的 Skill/工具身份必须固定：可使用原版本继续，或在工具被运维停用时明确阻塞，不能静默替换为新方法。已保存来源快照继续可追溯。

只读工具以超时、额度、错误率和证据质量作为观测指标；现有写入仍受原审批及幂等机制控制。验证失败时关闭新增入口并保留失败记录，旧工具与已有聊天路径继续可用。

### 10. 流程图更新

实施后更新 `images_ref` 中实施、Agent、聊天和 RAG 四张图；新增独立的“Skill 选择与加载”和“官方网页检索”流程图。图中应标注实际启用条件、固定业务步骤、可选工具、预算判断与来源不足出口。

六张流程图已更新到实际实现；实施 Agent、Skill 和官方网页工具分别独立成图。

参考：Agent Skills 的目录与元数据格式参见 https://agentskills.io/specification；按需加载机制参见 https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills 。具体实施以本项目上述契约和验收门槛为准。

### 11. 实际交付记录

2026-10-07 更新：

- 已实现四个可选工具与四个实施 Skill，接入 实施 Agent、原生聊天工具和 MCP。已增加创建/编辑材料检查和导入预览界面。
- 配置评审、迁移准备、角色培训练习和验收对照作为建议交付物保存。验收结论仍来自实际配置、成员、导入与审批记录；建议报告不会修改 `ready` 或代替审批。
- Skill 正文及资源在 Run 启动时固化，方法、调用和网页来源留痕；恢复仍使用原内容。Agent 分项额度在恢复后保留，成功动作重放不重复外部调用。
- 聊天可选工具按会话版本预约额度；网络返回后再检查账号、租户、项目权限、登录会话、归档和版本。来源只保存到私有调用记录，不伪造用户附件。
- 新迁移 `0031_conversation_tools`；Dockerfile/ignore 已补充 Skills 打包，Compose 已传递开关和网页配置。仅在隔离测试库验证迁移，本次未执行生产迁移。
- 六张流程图已生成到 `images_ref/`，含 PNG、SVG、Mermaid 和 Graphviz 源文件。

首版采用按节点的确定性 Skill 选择；尚未加入模型选择 Skill 的独立动作。搜索 Provider 使用 Brave，尚未加入其他 Provider 的配置选择。页面版本/发布时间保持未知，不做自动推断；需求矛盾检查只比较明确标注的人数，其他语义矛盾进入模型报告和人工核对。以上限制均在运行说明列出。

真实服务探针：2026-10-07（北京时间）模型认证失败 `MODEL_AUTHENTICATION_FAILED`；官方网页开关、搜索凭据和产品目录尚未配置。报告位于 `tests/artifacts/tool_skill_services.json`。直接读取官方公开文档的网络探针超时，报告位于 `tests/artifacts/official_fetch_probe.json`。这些项目保留待验收状态，不能宣称 P4 真实服务门槛或模型质量对比已经通过。

自动化结果：

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| 后端全量回归 | 462 通过、4 跳过 | 852.41 秒；可选浏览器/真实环境测试按原条件跳过；6 条现有迁移/反射警告。|
| 最新工具与关联回归 | 121 通过 | 新增工具、Skill、原生聊天、MCP、Agent、执行快照、迁移和 Worker；包括统一前的成功/失败验收场景，历史结果不代表本次代码已验证。|
| 补充 Provider 异常结果测试 | 1 通过 | 非字符串网址被忽略，不使工具链异常退出。|
| Edge 浏览器完整流程 | 1 通过 | 材料检查、只读预览、80 人导入、四个人工审批与交付、下载及执行追踪；与补充 Provider 测试一起运行共 2 通过，42.72 秒。|
| 前端类型检查与构建 | 通过 | `vue-tsc -b`、Vite build。|
| Ruff | 通过 | 整个 backend、新增服务探针及本次测试/迁移文件。|
| Mypy | 通过 | 本次主要实现的 11 个模块，`--follow-imports=silent`；不代表全库类型问题全部清除。|
| 四个 Skill 的 quick_validate | 通过 | YAML 元数据使用 `metadata.version`，受控资源及 Token 验证另由行为测试覆盖。|
| Alembic | 通过 | 空库/旧 head 升级、0031 回退与重新升级、`alembic check`。|
| Compose 配置 | 通过 | `docker compose config --quiet`。|
| Docker 镜像构建 | 未验证 | 本机 Docker 引擎未启动；未启动或修改生产服务。|

浏览器原始启动失败原因是 Playwright 附带 Chromium 未安装，已使用本机安装的 Edge 通过同一流程。最新核心回归加补充 Provider 测试共 122 个独立用例通过，浏览器用例另计；不与全量回归数量相加。
