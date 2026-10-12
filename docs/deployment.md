# 部署与运维

从项目根目录运行本文命令。API、Worker 和索引进程需要一致的代码、迁移及运行配置。开发栈和隔离验收环境不等于生产环境。

## 文档导航

[agent.md](agent.md) · [api.md](api.md) · [architecture.md](architecture.md) · [data_pipeline.md](data_pipeline.md) · [deployment.md](deployment.md) · [evaluation_report.md](evaluation_report.md) · [rag.md](rag.md) · [testing.md](testing.md)

## 本文内容

- [CPU 本地模型与索引迁移](#cpu-retrieval)
- [本地检索部署](#local-rag)
- [预生产初始化、备份与恢复](#preproduction)
- [索引运行、相关性策略与故障处理](#retrieval-operations)
- [2026-09-15 安全审查记录](#safety-review-20260915)

## 快速入口与模型缓存

Windows 本地启动入口为 `start.ps1`；开发容器组合见根目录 `docker-compose.yml`，隔离预生产见 `docker-compose.preproduction.yml`。环境模板为 `.env.example` 及 `config/` 中的示例，不能直接替代带完整服务配置的环境文件。

`knowledge` 已删除，API 镜像不再复制它。主机模型缓存默认改为 `data/models`；显式指定 `RETRIEVAL_MODEL_CACHE` 时仍使用指定目录。Docker 模型卷和 `/models` 配置保持现有部署方式。使用旧 `knowledge/models` 路径的自行配置需要改为新路径，再按显式下载流程准备权重。

部署准备与实际服务验证分开记录；邮件、对象存储、模型、备份和并发指标通过真实运行后才可纳入生产验收。


<a id="cpu-retrieval"></a>

## CPU 本地模型与索引迁移

企业知识采用 `BAAI/bge-small-zh-v1.5`（512 维）和 `BAAI/bge-reranker-base`，设备为 CPU，基线使用 FP32。回答生成模型单独配置。本次不默认启用 INT8；量化前后必须在相同真实资料上比较排序和延迟。

### 参数与行为

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

### 安装与模型准备

从项目根目录使用虚拟环境（以下为 Windows PowerShell）：

```powershell
.venv/Scripts/python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv/Scripts/python.exe -m pip install -e '.[rag-local]'
# 将 config/rag.local.env.example 合并到自己的部署环境文件，保留数据库、密钥及生成模型配置。
$env:SAAS_ENV_FILE='.env.rag-cpu'
.venv/Scripts/python.exe scripts/download_rag_models.py
.venv/Scripts/python.exe scripts/check_rag_config.py
```

下载脚本是显式联网步骤。运行时仅从本地加载，不隐式下载、不回退其他模型。默认缓存为项目绝对路径 `data/models`，可用 `RETRIEVAL_MODEL_CACHE` 覆盖；上线时将两个 `*_REVISION` 固定为已验收的模型提交版本。模型缓存不提交 Git。

真实检索需要 PostgreSQL/pgvector >= 0.8、OpenSearch、Redis 和已有 Celery Worker/Beat。SQLite 仍仅支持 mock。上传及重建只入队，Worker 分批编码，在全部向量与 BM25 发布成功后标记 ready；每批释放推理锁，让问答有机会执行。单进程内 embedding 与 reranker 串行推理。

### 迁移与重建

1. 停止旧版 API 和索引 Worker，按现有运维流程备份数据库。使用具有跨租户迁移权限的数据库账号运行 `python -m alembic upgrade head`；本项目 Alembic 读取 `DATABASE_URL`。
2. `0020_bge_cpu` 将向量列改为 `vector(512)` 并重建 HNSW。旧向量是派生数据，迁移置空，不把 1024 维截断为 512 维。原文、历史片段 ID 和证据快照保留。
3. 所有文档标记 pending，失败计数清零。新程序部署后启动索引 Worker/Beat，活动文档自动重建；待新索引 ready 前不返回旧版本。旧 OpenSearch 条目无法通过新的 SQL 索引身份校验。
4. 管理员可通过既有 `POST /api/knowledge/{id}/reindex` 重试失败文档。模型、前缀、长度或分块设置变化后也需要重建；这些配置包含在索引指纹中。

迁移 PostgreSQL 时使用 `row_security=off`，权限不足会失败，避免只迁移一部分租户。降级同样使派生索引失效，需要配合旧版模型重建；不会恢复旧向量。不要在新旧程序同时写索引时执行迁移。

### 独立 CPU 模型进程

需要让 API 和 Worker 共用一个常驻模型副本时，使用已有服务：

```powershell
# 此进程加载 local 配置，并分别设置 EMBEDDING_API_KEY、RERANKER_API_KEY。
.venv/Scripts/python.exe -m uvicorn backend.retrieval_server:app --host 127.0.0.1 --port 8010 --workers 1
```

API/Worker 使用 `config/rag.online.env.example`，两个 URL 指向该服务的 `/v1`，填写对应独立凭据和 BGE 模型名；online 在这里表示 HTTP 传输，权重仍在本机 CPU 上运行。调用方负责添加查询前缀，服务不会重复添加。此模式的建库使用上述近似分块。首次启动模型延迟和吞吐需实测；增加 Uvicorn worker 会复制模型并发占用 CPU 和内存。

### 验证

```powershell
$env:SAAS_ENV_FILE='config/rag.mock.env.example'
.venv/Scripts/python.exe -m pytest tests/test_retrieval_models.py tests/test_knowledge.py tests/test_chat_scope.py -q
```

模拟模型测试覆盖 512 维校验、前缀、窗口预算、标题输入、异步入队、表头及索引指纹，不证明模型质量。`tests/check_agent_postgres.py` 仅允许本机独立 `agent_rag_test` 库，验证实际迁移、向量存储和 RLS；运行前设置 `AGENT_TEST_POSTGRES_URL`。真实模型使用 `evaluations/retrieval_baseline.jsonl` 的 54 个候选问题，经业务人员复核后运行 `evaluate_retrieval`，记录 Recall@5、无答案准确率、均值与 P95 延迟。仓库测试不承诺 CPU 响应秒数。

<a id="local-rag"></a>

## 本地检索部署

本期不包含 PDF OCR。纯文字 PDF 提取页码与正文；扫描页拒绝上传，混合页只提取文字层并标注图片未识别。

### 当前实施状态

已实现模型适配层：本地 Qwen3 Embedding、本地 BGE、两类在线 HTTP 接口、LLM 的 responses/chat_completions 两种协议。企业知识索引的 real 路径已接通 PostgreSQL/pgvector + OpenSearch BM25 + RRF + 必选重排。mock 路径不调用向量或重排模型。

这不是完整 RAG 上线声明：项目材料/附件的统一异步索引、消息表迁移、查询改写与证据复核、长期摘要、全量迁移和质量评测仍按主计划推进。当前私有附件仍采用已有受权限控制的词项检索，不能把其检索结果称为已完成向量召回。勿将当前分支作为百万片段生产验收结果。

用户已决定暂不部署服务器。本次不下载权重、不安装 GPU 依赖、不改现有 `.env`、不迁移演示数据库。真实模型与 PostgreSQL/OpenSearch 的联合验收尚未执行。

### 配置方式

默认读取项目根目录 `.env`，也可设置 `SAAS_ENV_FILE` 指向明确的配置文件；操作系统环境变量优先。修改后重启进程。

现有 LLM 名称沿用 `MODEL_NAME/MODEL_BASE_URL/MODEL_API_KEY`，不要填写尚未实现的 `LLM_ONLINE_*` 字段。`LLM_MODE=local` 使用 `LLM_LOCAL_MODEL/LLM_LOCAL_BASE_URL/LLM_LOCAL_API_KEY`。`MODEL_API_STYLE` 对两种模式均生效。在线 qwen-plus 选 `chat_completions`，返回 JSON 后仍执行本地 schema 校验。

配置示例在 `config/rag.mock.env.example`、`config/rag.local.env.example`、`config/rag.online.env.example`。它们只包含 RAG 差异配置，不是完整生产配置；请合并数据库、身份验证等项目设置。全部地址、密钥需使用本项目自己的配置，不复制其他项目凭据。

`RAG_MODE=real` 要求 PostgreSQL/pgvector、OpenSearch 与真实检索模型；仅配置 `MODEL_MODE=real` 只启用回答大模型，不代表完整 RAG。`RAG_MODE=mock` 用于现有演示和测试。SQLite 不会冒充持久化向量检索。

运行只读配置检查：

```powershell
.venv\Scripts\python.exe scripts/check_rag_config.py
```

检查不访问模型、不下载权重，不显示密钥；配置完整不等于服务已预热或质量已验收。

### 后续启用本地模型时

1. 在独立推理环境安装项目 `rag-local` 可选依赖；PyTorch CUDA 版本须与目标机器兼容。
2. 由操作者明确下载 Qwen/Qwen3-Embedding-0.6B 和 BAAI/bge-reranker-v2-m3，锁定 revision 或使用本地目录。服务只读取本地缓存，不在用户请求中下载权重。
3. 设置对应模型模式为 local，设备明确为 cpu 或 cuda:0；设备错误不自动回退。
4. 两类模型各进程只加载一次，进程内加锁；不能通过增加 Web Worker 数量无意重复加载显卡模型。
5. 直接在平台进程使用本地模型适合开发。独立服务使用 `backend.retrieval_server:app`，默认建议仅绑定回环地址；平台用 online 模式访问。

示例启动命令（本文未执行）：

```text
python -m uvicorn backend.retrieval_server:app --host 127.0.0.1 --port 8100 --workers 1 --limit-concurrency 8
```

接口为 `/v1/embeddings`、`/v1/rerank`，必须配置服务访问密钥并通过 Bearer 认证。公开或跨主机访问前须配置网络访问控制和 TLS。生成、Embedding、Rerank 使用独立凭据，不能隐式复用。

平台 online 调用时将 BASE_URL 配置到 `/v1`，模型名必须与服务实际加载的模型完全一致。Embedding 的查询指令由平台添加，独立服务不重复添加。Rerank 服务按 tokenizer 将长片段分窗并取最高分；在线第三方服务须返回所有候选的完整排序，否则拒绝结果。

### 索引与模型兼容性

本期 real 索引签名区分模型、调用模式、revision、维度、查询指令、归一化和服务地址。修改任一项后需重新建库，不能用新查询向量查询旧索引。

OpenSearch 仅保存词项与过滤字段，返回 ID 后由数据库再次核对权限、有效版本与状态，再读取正文。当前词项采用中文双字和完整英文/项目标识符，BM25 由 OpenSearch 实际执行；jieba 领域分词评测属于后续优化。

原有 mock 索引不自动改写成 real，切换前应先完成备份、真实基础设施联调与迁移演练。现有 Docker Compose 已透传 HTTP 接入所需设置；平台容器默认不打包本地 GPU 权重。

协议参考：[Sentence Transformers](https://www.sbert.net/docs/package_reference/cross_encoder/model.html)、[千问结构化输出](https://www.alibabacloud.com/help/zh/model-studio/qwen-structured-output)。

### 生成模型选择更新

用户选择青云聚合平台提供的 `qwen-plus`，替换之前的生成模型选择。API 基础地址以该平台 API 接入说明为准，定价页面不是接口地址。已根据青云 TOP 国际站文档配置 MODEL_BASE_URL=https://top.qingyuntop.ai/v1，并将本地 MODEL_MODE 设为 real。用户只需填写 MODEL_API_KEY 后重启后端。尚未进行带凭据的真实调用或 JSON 协议验收。

接入地址依据：https://qingyuntopai.apifox.cn/doc-9285041 。最终聊天请求为 https://top.qingyuntop.ai/v1/chat/completions 。RAG 检索模式仍保持现状，启用生成模型不代表向量模型和重排服务已部署。

<a id="preproduction"></a>

## 预生产初始化、备份与恢复

此环境名为 saas-preproduction，使用独立数据库卷与 18xxx/19xxx 本机端口。不是生产发布配置；HTTP 仅绑定回环地址。远程开放前必须加 HTTPS、安全 Cookie 和网络访问控制。

### 启动

需要可运行 Linux 容器的 Docker Engine。本机主 Compose 已完成备份、迁移和业务冒烟验收；下述隔离预生产 Compose 及其独立卷、S3 恢复和故障演练仍需单独执行与记录。

```powershell
.venv\Scripts\python.exe scripts/init_preproduction.py
docker compose --env-file .env.preproduction -f docker-compose.preproduction.yml config --quiet
docker compose --env-file .env.preproduction -f docker-compose.preproduction.yml up --build -d
docker compose --env-file .env.preproduction -f docker-compose.preproduction.yml ps
```

生成器不覆盖已有 Secret，不在日志显示密码；.env.preproduction 用于迁移与 Compose，.env.preproduction.runtime 不含数据库 owner 连接。文件均被 Git 忽略；仅在本机读取管理员密码，不粘贴至聊天或报告。

入口：Web http://localhost:18080；平台登录 /platform/login；Mailpit http://localhost:18025；Grafana http://localhost:13000；Prometheus http://localhost:19090。

迁移容器先确保应用数据库角色，再升级 Alembic、初始化 LangGraph 检查点并授予 DML。API/Worker 不执行 DDL。模型默认 deterministic；实际模型运行需在 runtime 文件设置 MODEL_MODE=live、MODEL_BASE_URL、MODEL_NAME、MODEL_API_KEY 和 EMBEDDING_MODEL，并重启相关容器。不要将凭据提交版本库。

### 多角色验收

1. 平台管理员创建测试公司，从 Mailpit 取得首位公司管理员邀请。
2. 公司管理员邀请实施顾问、独立审批人、客户联系人；创建 80 人项目，明确部门与迁移范围。
3. 公司管理员启动 Run，独立审批人审核计划；实施顾问可在配置审批前修订方案，原批准材料不得复用。
4. 配置批准后，顾问上传 80 人 CSV，包含一名 admin；部门须与目标配置一致。
5. 独立审批人批准导入，核对实际成员与交付物；客户联系人提交反馈，独立审批人完成验收。
6. 重复上传/跨项目审批/未授权访问必须失败；人数不足时必须阻塞项目，不能关闭。

### 备份与恢复

backup 容器每天创建整库 custom-format dump，并检查可读取目录。输出到 backups/preproduction，last_success.txt 记录最后成功时间。当前不自动删除历史备份，也不提供 PITR。

恢复演练只创建新的 saas_restore_<timestamp> 数据库，不覆盖源库：

```sh
docker compose --env-file .env.preproduction -f docker-compose.preproduction.yml exec \
  -e BACKUP_FILE=/backups/saas_preproduction_<timestamp>.dump backup \
  sh /scripts/restore_preproduction.sh
```

在无写入的样例环境比对项目、Run、步骤、成员和交付物数量；同时下载并校验 S3 对象。PostgreSQL dump 不包含 S3 对象，需另外备份 artifacts 卷。记录备份时间、恢复耗时、比对结果；未完成这些步骤不能声明 RPO≤24h/RTO≤4h 达标。

### 故障与观测

- 在测试 Run 执行期间重启 worker，核对步骤序号、审批材料和成员唯一约束；Outbox 同一事件重复投递不得重复写入。
- 停止 Redis 后 API 应保留 Outbox；恢复后重新投递。终止任务有明确失败类型，依赖错误最多自动尝试三次。
- SMTP 在发送前提交 sending 状态；发送结果不明时停留在 sending/unknown，禁止自动重发。先核对 Worker 和 SMTP 记录，再人工处理；Message-ID 不提供跨系统幂等保证。
- 检查 Prometheus 告警和 Grafana 面板。0030 起 Outbox 传递 W3C 上下文，Collector 将 API/Worker Trace 写入 Tempo；Grafana 的 Execution Traces 数据源可按 Trace ID 查询。外部告警接收器仍需单独验证。
- 如果执行人权限在排队期间被撤销，禁止 Worker 提升权限继续执行；检查 Outbox 错误并由有权限的项目经理取消/重试。

### 兼容与回退

旧导入任务的 run_id 保持 NULL 供历史查询，不能冒用新审批执行；须新建 Run 重新校验。新旧待审批材料契约不兼容，升级前完成或取消旧的待审批 Run，不自动追认旧批准。

迁移 0010–0013 是扩展式升级，降级会删除本轮新增数据表；先备份并确保无等待材料或未投递任务，优先恢复备份到隔离库验证，不在真实环境直接降级。

平台离线评测执行接口现要求 `Idempotency-Key`；相同账号的重复请求键返回原报告。Trace 页面显示持久化节点事件，不提供未采集的耗时或跨服务 Span，不替代 Collector/Worker 运行验证。

<a id="retrieval-operations"></a>

## 索引运行、相关性策略与故障处理

RAG 查验、聊天及 Agent 的企业和项目知识文档共用 知识检索 最终证据。聊天附件、交付物、平台手册和实时状态仍按各自授权路径读取。知识文档索引未就绪时返回 `index_unavailable`，不会静默使用其他知识索引。

### 相关性判断

本机默认 `RETRIEVAL_RELEVANCE_MODE=rules`，使用重排分数与 `RETRIEVAL_MIN_RERANK_SCORE=0.5` 的规则阈值；接口返回 `quality_calibrated=false`。该分数不代表概率或独立质量验收。更换重排模型后须重新检查阈值。

`RETRIEVAL_RELEVANCE_MODE=calibrated` 要求固定模型版本、校准发布文件、锁定验证报告和独立复核；任一条件不满足即返回 503。发布门槛逐格式检查 MD、TXT、DOCX、CSV、JSON、PDF、PPTX 和 XLSX。当前 192 个合成边界问题仅用于结构与流程回归，独立复核、真实资料评测和两模型对比仍未完成。详情见 `docs/testing.md#retrieval-regression`。

### 索引恢复

Docker 使用 `RETRIEVAL_INDEXING_ENABLED=true`，由单实例 beat 调度、indexer 消费索引任务。API 内置索引循环保持关闭。历史文本资料可分批注册；只有提取文本而没有原件的 PDF、PPTX 和 XLSX 必须重新上传。失败资料在详情页显示原因；有权限的项目成员或公司管理员可重新加入队列。模型身份变化后会重建不兼容的向量。

### 验证与故障处理

检查 `/api/knowledge/inspect` 的 `pipeline=retrieval`、`chunks` 与 `evidence_text`，再用真实资料核对页码、幻灯片或工作表位置和聊天、Agent 引用。用 `/metrics` 观察 pending、failed、最旧待处理时间与解析耗时。索引故障先修复消费者和原件，再重试失败资料。需要暂停新索引时设置 `RETRIEVAL_INDEXING_ENABLED=false`；校准策略故障可将 `RETRIEVAL_RELEVANCE_MODE` 设回 `rules`，保留已写入的索引数据。

<a id="safety-review-20260915"></a>

## 2026-09-15 安全审查记录

> 历史审查：下文的缺项描述属于 2026-09-15；随后完成的执行快照、运行身份检查和隔离恢复验证见 evaluation_report.md。仍不能据此宣布生产验收完成。

### 结论

六项尚未全部达标，不能据此宣布生产验收通过。本次只修改安全约束，没有扩展产品功能，也没有向运行中的环境部署或迁移。

### 本次修补

1. 邮件在调用 SMTP 前，以条件 UPDATE 领取并提交 `sending` 状态。只有 `queued` 能被领取；并发领取者不能重复发送。成功后记录 `sent`；异常记录 `unknown`，不自动重试。进程中断留下 `sending`，同样不自动重发。固定 Message-ID 不代表接收服务器支持幂等。
2. 数据库新增 `uq_agent_run_active_project` 部分唯一索引：一个项目最多一个 pending/running/preparing_materials/waiting_approval/blocked Run。迁移 0024 遇到已有重复活跃 Run 会失败，不能自动取消未知执行状态的 Run。
3. 结构化模型和文本流入口共享不可信数据规则，明确覆盖知识库、附件、Memory、Tool Result。工具白名单、阶段与审批仍由代码校验。提示词不构成防注入安全证明。

### 六项检查及未完成工作

| 项目 | 已有实现 / 发现 | 尚需补齐与验收 |
| --- | --- | --- |
| 跨系统幂等 | Worker 节点提交与 Outbox 重投；配置与导入主要写同一数据库；邮件原有提交窗口可重复发送，已修补为结果不明时停止 | SMTP 不能同时保证不丢与不重；需要支持幂等键的邮件提供方或人工对账。配置/导入重放仍可能产生重复审计事件；外部存储和索引需要逐一验证提交前后崩溃窗口 |
| 租户隔离 | db_role 设置 NOSUPERUSER/NOBYPASSRLS；API 使用事务级 set_config，并在新事务恢复请求上下文；新检索表有 FORCE RLS | 旧表未统一 FORCE RLS；运行身份缺少实际角色、可继承角色、表所有权的启动检查。需要真实 API/Worker 角色验证无上下文拒绝、跨租户读写拒绝及同一池连接 commit/rollback 后切换租户 |
| Run / 审批快照 | 审批保存 payload/material_hash，决策和执行时重新核对材料 | Run 仍读取实时 requirements_text、知识检索与模型配置。需独立不可变快照保存文档正文/版本/哈希、审批规则、模型固定版本、提示词与检索版本及模型输入输出。仅记录可变模型别名不能保证复现；不要为历史 Run 补造“当时快照” |
| RAG 检索前权限 | 知识检索 向量 SQL 使用 tenant + document IDs；BM25 使用 tenant + 授权 ID filter；统一来源 BM25 也有前置 filter，SQL 回查是第二道防线 | 必须覆盖所有生产检索入口和附件/Memory路径，测试未授权高相关文档不会进入候选集及 reranker。不能只凭返回结果不含秘密判断通过 |
| Prompt Injection | 新增公共数据边界提示；现有工具阶段、参数与审批检查 | 还需覆盖原生工具协议入口与间接授权升级路径，用恶意附件、Memory、工具结果验证实际工具调用无法越权；不能把关键词扫描或提示词视为充分防护 |
| 并发和灾备 | 单项目单活跃 Run 已补数据库约束；已有节点恢复测试；恢复脚本恢复至新数据库 | 尚未执行真实 Worker kill、备份恢复后 Outbox 重放、索引删除重建与权限一致性演练。恢复脚本目前核对版本和表计数，不能证明业务恢复；需明确 RPO/RTO 并记录实测结果 |

### 邮件运维约定

`unknown` 表示一次 SMTP 尝试失败但不能证明未被接收；`sending` 可能仍在执行，也可能进程已中断。先核对 Worker 和 SMTP 日志，再人工处理；不能将这些状态批量重置为 queued。当前选择防止自动重复的语义，可能留下未送达邮件；这不是 exactly-once delivery。unknown 清除链接正文；中断留下的 sending 正文仍需要按令牌生命周期清理。

### 本次验证记录

- 邮件、Worker、交付：11 项通过。新增“SMTP 已接受后进程异常退出，再执行 flush 不重复调用 SMTP”故障注入测试。这是进程中断模拟，不是真实 SIGKILL 演练。
- 并发约束、Agent、模型、聊天流、聊天范围、知识检索：首次 46 通过，1 失败。失败源于测试假设 Responses 协议但继承本地 Chat Completions 配置；测试显式固定协议后，模型测试文件 3 项通过。
- 新增直接绕过 API 插入第二个活跃 Run 的数据库约束测试；当前使用 SQLite，不能代替 PostgreSQL 并发事务测试。
- 确认本机存在 PostgreSQL、OpenSearch 与预生产容器；未修改这些容器，未执行真实 PostgreSQL 安全/灾备演练。
- 工作区已有大量未提交变更，本次没有回滚或提交其他工作。

### 上线顺序

先补不可变快照与运行账号校验，再补外部效果对账和所有检索/模型入口的攻击回归。最后在独立 PostgreSQL/OpenSearch 环境演练崩溃、恢复与索引重建，记录数据一致性、重复副作用和恢复耗时；六项逐项有证据后再验收。
