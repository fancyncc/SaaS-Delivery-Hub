# 评测与验收报告

当前可以确认已有工程功能与隔离环境验证记录，且已完成一次公开 CMRC 指标检测。生产效果验收仍未闭环：`production_acceptance=false`。不同日期、代码版本和数据集的指标不能合并为一次生产验收。

## 文档导航

[agent.md](agent.md) · [api.md](api.md) · [architecture.md](architecture.md) · [data_pipeline.md](data_pipeline.md) · [deployment.md](deployment.md) · [evaluation_report.md](evaluation_report.md) · [rag.md](rag.md) · [testing.md](testing.md)

## 本文内容

- [2026-10-12 CMRC 2018 实测结果](#cmrc-20261012)
- [工程交付实现与验收记录](#delivery-acceptance)
- [执行安全、服务与隔离恢复验收](#execution-safety)
- [历史留出集首轮结果](#holdout-v1-results)
- [2026-09-27 至 28 开发题库对比](#business-results-20260928)
- [2026-09-28 本机技术验收](#local-acceptance-20260928)
- [2026-09-28 检索详细测评](#retrieval-report-20260928)

## 阅读范围

| 范围 | 已有证据 | 当前限制 |
| --- | --- | --- |
| 工程功能 | 后端、Agent、记忆/上下文及浏览器业务流程的回归记录 | 以具体记录的版本和环境为准 |
| 隔离运行 | PostgreSQL 隔离、Worker 恢复、备份还原和分布式追踪记录 | 不代替生产环境或真实外部服务验收 |
| CMRC 公开指标 | 固定 200 题真实检索/生成；EM 70.5%、F1 87.622%；5 次生成超时计零 | 仅 182 段原文，串行测试；未完成全开发集、并发、语义引用及完整费用验证 |
| 历史检索观察 | 开发对比与首次留出集记录 | 资料已参与分析，历史 86.7%/88% 不能代替当前验收 |
| 生产验收 | `production_acceptance=false` | 仍需真实服务、业务效果及实际性能/费用等证据闭环 |

2026-10-12 本次文档整理移除了 `knowledge` 目录，并调整默认主机模型缓存路径。它不重跑或改写以下任何历史评测数据；不要把原实测的代码一致性结论扩展到整理后的全部代码。


<a id="cmrc-20261012"></a>

## 2026-10-12 CMRC 2018 实测结果

> 本节属于记录的测试版本。后续文档整理及默认模型缓存路径调整未触发新的模型评测；原始记录和冻结代码哈希保留不变。

2026-10-12 16:24–17:42（Asia/Shanghai），使用固定随机种子 2018 从公开开发集抽取
200 题，将其对应的 182 段原文放进同一个临时知识库，运行当前检索与生成程序。
每题搜索整个准备好的知识库。标准答案和原标注位置仅用于离线评分，不提供给模型。

全部 200 题均已记录；195 题取得模型回答，5 题发生生成服务 `ReadTimeout`。
失败请求的答案计零分，没有删除或单独补跑。记录的 `status=failed`、`complete=false`
表示请求没有全部成功；它不表示只评分了部分题目。记录中没有遗漏的题目 ID。

| 指标 | 结果 | 分母或说明 |
|---|---:|---|
| 答案 EM | **70.500%** | 200 题，含 5 个失败请求 |
| 答案 F1 | **87.622%** | CMRC 官方 v6 混合分词与最长连续匹配规则 |
| 重排后答案位置命中率@1 | **97.5%** | 195/200；仅限本次 182 段原文的知识库 |
| 重排后答案位置命中率@3 | **100%** | 200/200；仅限本次 182 段原文的知识库 |
| 重排后答案位置命中率@5 | **100%** | 200/200；仅限本次 182 段原文的知识库 |
| 最终证据答案位置命中率 | **98.0%** | 200 题；要求正确原文及标注字符位置 |
| 请求错误率 | **2.5%** | 5/200，均为读取超时 |
| 检索 P50 / P95 | **8.63 / 14.96 秒** | 200 次 |
| 生成 P50 / P95 | **8.47 / 16.31 秒** | 195 次成功生成；超时仍计入端到端统计 |
| 端到端 P50 / P95 | **17.93 / 37.42 秒** | 200 次，含失败请求 |
| 已返回计量的输入 Token | **781,361** | 195 次请求的提供方回执 |
| 已返回计量的输出 Token | **12,164** | 195 次请求的提供方回执 |

这里的“100%”表示：每题前三条重排候选中，至少一条来自正确原文且包含一个有效的
标准答案字符位置。它不要求前三条全都正确，也不代表生成回答正确、最终选用证据完整
或引用正确。本次仅收录抽样题目对应的 182 段原文，每题答案均保证在库中；没有加入
开发集中其余原文作为干扰资料，不能据此声称完整开发集或生产知识库的命中率为 100%。

针对这一结果，另用未调用后端评分函数的独立程序逐题核对原文、字符位置及记录的候选：
195 题的首个有效命中在第 1 位，5 题在第 2 位，与原报告一致。逐题证据见
`hit-audit.json`。这是对已有记录的复核，没有重新请求模型或运行检索。

5 个超时请求没有返回 usage，Token 统计因此不覆盖所有已发出的请求。
没有提供实际服务的输入/输出计费单价，费用保持“未测量”，未用估计值填充。

### 配置与核对

- 生成模型：`qwen-plus`；使用当前应用的模型适配器和引用校验。
- Embedding：`BAAI/bge-small-zh-v1.5`，提交 `7999e1d3359715c523056ef9478215996d62a620`。
- 重排模型：`BAAI/bge-reranker-base`，提交 `2cfc18c9415c912f9d8155881c133215df768a70`。
- 最终证据预算：1,200；使用当前 `rules` 配置，没有根据本次结果调整检索参数。
- 数据版本：`c0eb1b6ba219847457e6af3180da722bbeb656af`；原始文件 SHA-256：
  `86000878d2e8b535e33b36161a3032d74398881d8ee3ae16b2b22f1a48a99cc6`。
- 所有 200 题具有至少一个有效原标注位置。11 条个别位置标注不准确，保留原值，
  使用同题其他有效位置评分。没有人工重写标准答案。
- 用官方 v6 脚本对相同的 200 题和预测字典再评分，EM/F1 **完全一致**，TOTAL=200、SKIP=0。
- 本报告初次发布时，后端与 Python 评测程序的文件哈希与实际运行快照一致。运行期间只完善了开发依赖、
  启动脚本的失败后评分处理和测试自包含性；差异逐项保存在 `verification.json`，
  没有改变本轮的检索、生成或评分算法。
- 新增评测测试 10 项通过，相关现有回归 42 项通过，Ruff 和 PowerShell 语法检查通过。
- 临时租户的 SQL 数据已回滚，生成的 OpenSearch 索引条目已删除并核对。

### 报告与复跑

运行 ID：`eb7c4e6f4245435286116a923ddc8b80`。

本地完整记录位于 `data/evaluations/cmrc-200-final-20261012/`：

- `run.json` 与 `run.json.run/`：逐题证据、回答、耗时、提供方回执及冻结输入。
- `score.json`、`score.md`：完整指标与逐题自动评分。
- `score-predictions.json`、`selected-official-dev.json`：供官方脚本复核的相同题目和预测。
- `verification.json`：官方评分一致性、代码快照核对和失败类型。
- `hit-audit.json`：独立复核的逐题首个命中排名、原文区间及标准答案位置。

准备与运行命令见 [README](testing.md#cmrc-benchmark)。复跑时使用新的输出目录。
结果反映本次 200 题、182 段原文的公开开发集 RAG 测试。完整公开开发集有 3,219 题，
本次没有运行全量，也没有进行并发压测。这些指标不签发生产验收。

原始数据与评分规范：[CMRC 2018](https://github.com/ymcui/cmrc2018)，
[官方 v6 评分程序](https://github.com/ymcui/cmrc2018/blob/c0eb1b6ba219847457e6af3180da722bbeb656af/baseline/cmrc2018_evaluate.py)。
数据及改编评分工具遵循 CC BY-SA 4.0，归属 Yiming Cui 等作者；许可见 [LICENSE](../evaluations/cmrc2018/LICENSE)。

<a id="delivery-acceptance"></a>

## 工程交付实现与验收记录

更新：2026-09-06。目标：内置模拟 SaaS 真实持久化、独立审批人验收、完整功能与预生产验证；不发布生产。

### 当前事实

已实现配置写入/回读、材料绑定审批、实际成员导入、材料准备状态、版本交付物、真实上线检查、整改材料修改、配置方案重新审批、角色化待办、知识管理、SMTP 队列、S3 适配、Celery/Outbox/LangGraph 执行和预生产部署配置。

本地离线模式的 80 人三角色浏览器主流程已通过：公司管理员启动，独立审批人审批，实施顾问提交 CSV，最终数据库中有 80 名业务成员和 6 份交付物。此结果不等于 PostgreSQL、真实模型或完整预生产验收通过。

### 需求追踪

| 编号 | 需求 | 实现与证据 | 状态 |
|---|---|---|---|
| FR-01 | 独立身份、权限与状态机 | 原有回归、mypy 修复；新增 preparing_materials | 本地已验证 |
| FR-02 | 真实配置与快照 | delivery 工具、configuration API、配置回读断言、旧审批失效和版本冲突测试 | 本地已验证 |
| FR-03 | 审批与材料绑定 | project/run/kind/material_hash，全量快照比较 | 本地已验证 |
| FR-04 | 成员 CSV | run_id 必填、2 MB、部门/角色/冲突、真实写入、错误报告 | 本地已验证 |
| FR-05 | 部分失败恢复 | retry-failed API 与页面；新 Run/新材料、原任务关联、成功成员保留、幂等请求 | 本地补偿入口与重放已验证；真实故障注入待环境 |
| FR-06 | 实际验收与交付 | 真实人数/管理员/配置/导入/材料检查；6 类版本交付物 | 本地浏览器与 API 已验证 |
| FR-07 | 页面及角色待办 | 材料、差距、甘特、配置、导入、反馈、下载、后台状态、项目筛选 | 已实现，核心浏览器已验证 |
| FR-08 | 异步执行 | Outbox、Celery、LangGraph、逐节点事务、重复任务与中断测试 | 本地重放已验证；真实 broker/PG 故障演练待环境 |
| FR-09 | 知识检索 | 公司隔离、最新版本、来源授权、PG FTS/pgvector、排名融合 | 离线隔离已验证；PG 向量查询与真实召回指标未验收 |
| FR-10 | 智能节点 | 需求/差距/计划结构化调用、Prompt 版本、引用校验、失败退出 | 模拟模型失败测试通过；真实模型凭据未提供 |
| FR-11 | 邮件与对象存储 | 令牌与邮件同事务、SMTP 重试状态、S3 校验 | 故障替身测试通过；真实服务验收待容器 |
| FR-12 | Trace 与评测视图 | 独立节点轨迹/筛选/JSON 下载；平台离线评测运行/历史/版本对比/下载，角色隔离及请求幂等 | 本地 API 与浏览器通过；分布式 Span 聚合、真实模型实验未完成 |
| NFR-01 | 安全与运维 | 独立环境、随机 Secret、运行时不含 DB owner 凭据、备份/恢复脚本 | 配置校验通过；PG 权限/恢复未验收 |
| NFR-02 | 监控 | Prometheus 指标/规则、Grafana 仪表盘、OTel 导出配置 | 已实现基础；告警通知通道与跨 Worker Trace 未验收 |
| NFR-03 | 迁移 | 0010–0013；空库、0013→0009→0013 与 Alembic check | SQLite 通过；现有 PG 数据副本未验收 |

### M0–M5 门槛

- M0：文档基线和类型修复已落实；PostgreSQL/Compose 启动门槛仍缺证据。
- M1：内置 SaaS 业务闭环在 SQLite 通过。部门、模板、状态、自定义字段名称通过配置文档持久化；业务角色采用固定 admin/manager/member/viewer，尚非完整可自定义 RBAC 产品。
- M2：主要页面和多角色离线闭环已验证；独立整改任务实体、失败行专用重试及 Markdown/JSON 下载已补齐。Trace 与离线评测产品页已实现，真实模型实验及跨 Worker Span 聚合仍需补齐。
- M3：执行、检查点、Outbox 与邮件已接入。本地模拟故障已验证；真实 Celery 杀进程及跨 PG 检查点恢复未验收。
- M4：适配器和知识接口已接入；65 篇授权资料、模型地址/名称/凭据、真实 embedding 及质量基线缺失，不标记完成。
- M5：预生产 Compose、Secret、监控和恢复脚本已交付。本机主 Compose 已完成备份、迁移及业务冒烟验收；隔离预生产环境的完整故障演练和业务签署尚未完成。

### 验证入口

2026-09-06 结果：后端 `51 passed, 1 skipped`；独立浏览器场景覆盖原 80 人主流程、Trace 页面与 JSON 下载、平台评测运行与报告下载；Ruff、mypy（33 个源文件）、Vue 类型检查、Vite 构建通过。新增测试覆盖平台评测历史隔离、审计员只读、请求幂等及 Trace ID 解析。本轮未新增迁移，也未执行真实模型或预生产服务验收。

2026-09-05 本轮结果：后端 `47 passed, 1 skipped`（跳过项为显式启用的浏览器测试）；浏览器三角色 80 人流程单独通过；Ruff、mypy（33 个源文件）、Vue 类型检查及 Vite 构建通过。SQLite 空库升级至 0013、回退至 0009 后再升级、Alembic check 均通过。失败行测试使用注入的部分执行记录验证补偿行为，不代表真实 Worker 杀进程故障演练通过。

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check backend tests
.venv\Scripts\python.exe -m mypy backend
# web 中执行类型检查与构建后：
$env:RUN_BROWSER_TESTS='1'
$env:PLAYWRIGHT_CHANNEL='chrome'
.venv\Scripts\python.exe -m pytest -q tests/test_browser_flow.py -p no:cacheprovider
```

浏览器测试使用独立测试数据库与 18111 端口，不使用真实客户数据。截图写入忽略目录 tests/artifacts。不得将离线通过结果记为真实模型/生产验收。

### 仍需完成

1. 在可用 Docker Linux 环境执行预生产构建、PG 全链迁移、RLS/最小权限检查及备份还原；记录实际 RPO/RTO 和容量结果。
2. 提供模型配置及 65 篇授权知识，执行结构化成功/失败、向量召回、引用准确性与人工复核评测。
3. 补齐可自定义业务角色和字段类型、真实模型实验记录与分布式 Trace 聚合；已有页面只展示持久化节点记录及离线检索评测，不把记录创建时间当成节点耗时。整改任务只由实际检查结果关闭，不因提交反馈自动关闭。
4. 补齐完整跨 Worker Trace、告警通知路由、备份年龄指标和 PITR；现有定时备份为整库快照，不是 PITR。
5. 完成真实 broker/对象存储/SMTP 故障注入及并发测试，再关闭 M3/M5。

### 接口参考

本轮新增入口：Run 详情中的“查看执行 Trace”（`/app/runs/:id/trace`）；平台管理中的“评测历史与对比”（`/platform/evaluations`）。平台超级管理员可运行离线评测，平台审计员只能查看与下载，客户账号无权访问；平台历史只查询全局评测，不混入租户专有结果。POST `/api/platform/evaluations/run` 现在要求 `Idempotency-Key`，相同账号与请求键返回原记录。对比要求数据集、语料校验和及评测器版本一致。

结构化模型接口按 [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) 实现；异步框架参考 [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 和 [Celery tasks](https://docs.celeryq.dev/en/v5.5.0/userguide/tasks.html)。外部服务调用仅在显式配置后启用。

<a id="execution-safety"></a>

## 执行安全、服务与隔离恢复验收

日期：2026-10-02；对齐复核：2026-10-03。本轮范围为执行材料固化、跨 Worker Trace、真实服务检查及生产可靠性验证。

### 已加入的能力

#### 不可变执行材料

迁移 `0030_execution_safety` 新增 `run_snapshots`、`agent_runs.snapshot_version` 和 `workflow_outbox.trace_context`。

新建 Run 保存原始需求、需求文档正文、输入哈希、模型/检索配置及提示词身份。后续节点读取该输入；项目资料变更不会悄悄改变已启动执行。模型或检索身份变化时阻止继续推进，需要恢复配置或建立新的执行重新审批。凭据轮换不改变输入身份；快照不保存服务 API 密钥。

每次计划/配置/导入/验收材料提交均建立独立修订，审批绑定输入快照及修订 ID/哈希。导入材料包括原 CSV 和校验映射；修改正文、文件、映射、目标 Run 或审批 payload 不能复用原批准。配置修订仍通过原有取消旧审批、生成新审批流程。

执行轮次中的检索证据保留原文、版本与查询。结构化模型保留实际请求 payload、提示词/Schema、供应商返回模型标识、输出与用量。记录属于授权项目的私有材料，通过 Run 权限检查后查看；通用 Trace 不包含正文或密钥。请求记录与工作流节点共用事务：整轮事务被回滚时，该轮尚未提交的审计也会回滚；外部模型账单的完整对账仍需供应商记录。

数据库触发器拒绝修改快照和降低已保护 Run 的版本。输入快照每个 Run 仅有一份。删除快照仅允许原项目已软删除并超过 30 天，由现有清理流程处理；审批变更不删除历史修订。已存在 Run 保持 version=0，明确显示为历史未保护执行，不补造当时输入。

接口：

- `GET /api/runs/{id}/snapshots`：版本、状态和修订摘要。
- `GET /api/runs/{id}/snapshots/{snapshot_id}`：授权范围内的材料详情。
- 执行追踪页显示输入/材料修订、内容哈希、实际节点耗时和 Trace/Span 标识。

模型服务只提供可变别名时，保存输入与返回信息也不能保证未来生成相同输出。

#### 数据库运行身份

API 启动和 Celery 任务检查真实 PostgreSQL 身份、可切换/继承角色、业务表所有权、TRUNCATE 权限和核心表 RLS/FORCE RLS。拒绝超级用户、BYPASSRLS、创建角色/数据库权限、业务表所有者和携带迁移 owner 连接的运行配置。SQLite 保留开发模式，并明确不代表生产 RLS 验收。

迁移补齐 11 张核心业务表 FORCE RLS，新快照表同时启用 RLS/FORCE RLS。运行身份检查覆盖这 12 张表，以及既有迁移已经保护的 `agent_actions`、`agent_memories`、`agent_lessons`，合计 15 张表列于 `backend/runtime_guard.py`；这不是所有业务表的完整安全认证。认证目录、后台 Outbox 及其他服务级表仍需完整角色/服务能力审计。

Worker 在每次事务开始时恢复经重新授权的租户/用户上下文，执行前检查公司身份和项目权限。与 HTTP 入口一致，拒绝已软删除的用户/租户、非客户账号及无效个人空间所有权，返回正确的空间类型。快照不能延续已经撤销的授权。

#### 跨 Worker 追踪

HTTP → Outbox → Worker → 工作流节点/模型/检索/对象存储可共享 W3C Trace 上下文。Outbox 保存上下文，重复消息无法指定新租户。阶段审批产生的新请求拥有新的 Trace，使用 Run ID 关联不同阶段。

节点记录中的 duration_ms 来源于单调时钟，统计到节点记录写入前，不包含随后提交事务和排队时间；历史未采集项显示未采集。异常只记录类型，不导出可能包含凭据或正文的异常消息。强制终止进程可能损失未导出的 Span；持久化节点记录仍为业务恢复依据。

预生产 Compose 增加 Tempo 2.7.1、7 天 Trace 保留与 Grafana 数据源。Collector 使用 `otlphttp/tempo` 导出；Tempo 位于内部容器网络，无主机公开端口。管理员通过 Grafana 查询，应用用户不能借此绕过项目权限。默认开发 Compose 可通过下述 observability overlay 启用同一链路。

```powershell
docker compose -f docker-compose.yml -f docker-compose.observability.yml --profile agent up -d --build
```

依据：[PostgreSQL 16 RLS](https://www.postgresql.org/docs/16/ddl-rowsecurity.html)、[OpenTelemetry Python 上下文传播](https://opentelemetry.io/docs/languages/python/propagation/)、[Tempo 2.7.1 配置示例](https://github.com/grafana/tempo/blob/v2.7.1/example/docker-compose/local/tempo.yaml)。

### 运行验收

#### 真实服务探测

在正确运行环境中使用当前应用角色与已有配置：

```powershell
.venv\Scripts\python.exe scripts/verify_runtime_services.py
.venv\Scripts\python.exe scripts/verify_runtime_services.py --exercise
```

默认只检查连接与健康；exercise 允许少量真实生成/向量/重排调用、临时 S3 对象读写删除，以及 60 秒过期的 Worker canary。输出位于忽略目录 `data/acceptance/runtime_services.json`，仅含通过状态、范围和脱敏错误类型。

检查分别报告 passed、failed、not_configured、not_run。SMTP 默认只验证连接认证，不发邮件；cluster health 不等于检索质量；结构化协议成功不等于回答质量。报告永远不会自动设置 production_acceptance=true。

#### 隔离 PostgreSQL、真实进程故障和恢复

需要 Docker 引擎与缓存 `saas_agent-api` 镜像。脚本用当前源文件只读挂载运行该镜像；依赖版本改变后应先构建相应镜像。PG、Redis、Mailpit、MinIO 与 Tempo 使用独立临时容器、随机凭据和回环端口：

```powershell
.venv\Scripts\python.exe scripts/run_isolated_execution_acceptance.py
```

脚本执行：空库迁移 → 实际最小权限角色 → 无上下文拒绝/租户切换/并发启动 → SQL 快照篡改与保护降级拒绝 → Celery 完成并提交第一节点 → 真实 SIGKILL → 重启并重投同一 Outbox → 原已提交节点保留且没有重复序号，并核对 5 条成功动作台账。恢复后等待本次 API/Worker Trace 在 Tempo 中落库。随后验证 SMTP 合成邮件在隔离 Mailpit 收件 → 停止写入 → pg_dump/新库还原 → 比较业务与权限内容哈希 → 从文件备份还原生成材料到新桶。当前脚本使用统一实施 Agent；下文历史演练结果不代表本次脚本已经重新完成真实服务验收。

日志/报告只用于合成数据隔离环境；程序 finally 仅清理本次创建的随机容器/网络，并删除临时凭据文件。输出 `data/acceptance/isolated_execution.json` 和 `postgres.json`。诊断日志和合成备份留在忽略目录。SMTP 收件发生在独立 Mailpit 内，不证明客户正式邮件提供方的收件质量。

#### 恢复内容核验

用迁移/备份角色在暂停写入期间生成基线，和备份放在一起；凭据通过本地环境变量设置，不能粘贴到报告：

```powershell
# RESTORE_VERIFY_DATABASE_URL 指向本地受控连接；下例不包含凭据。
.venv\Scripts\python.exe scripts/verify_restored_database.py --capture data/acceptance/manifest.json
# 改为还原后的 saas_restore_* 新库，再执行：
.venv\Scripts\python.exe scripts/verify_restored_database.py --expected data/acceptance/manifest.json
```

只读、repeatable-read 事务计算规范化行哈希；设置 row_security=off，遇到会被 RLS 过滤的角色则失败，避免把部分数据当成完整备份。会话统一使用 UTC，避免还原库时区不同导致时间字段误报。涵盖项目、审批、材料、执行、Outbox、Agent 动作/经验/教训、请求幂等记录、成员、任务、租户身份及权限关系。输出仅包含表计数和哈希，不导出客户正文或用户密码。相同数量但不同审批/配置/数据会被识别。旧清单缺少新增覆盖表时核验失败，需要重新生成基线。

该工具不执行数据库恢复，核验目标必须命名 saas_restore_* 或 saas_acceptance_*；对象文件、RPO/RTO、告警和恢复后的服务授权仍需另外验证。

### 本轮证据与剩余工作

已有隔离实测证据：PostgreSQL 0030 空库迁移、15 张表运行账号检查、租户隔离和连接池事务切换、并发 Run 启动约束、SQL 快照修改/删除/保护降级拒绝、已提交节点重放、旧工作流/Agent 真实 Worker SIGKILL 恢复、Redis 消费、S3 读写、隔离 SMTP 收件、API/Worker Trace 存储，以及数据库和材料恢复。

用户更新配置后，使用全新进程重新读取当前项目 `.env`，确认生成模型密钥、地址、模型和协议配置没有被进程环境变量覆盖。结构化调用和独立最小请求仍返回认证失败 / HTTP 401；不存在密钥首尾空白或已知占位符。结果保存于 `data/acceptance/live_generation.json`；真实生成协议与回答质量尚未验收。

本机真实检索服务的 512 维向量及重排协议检查通过，OpenSearch 集群健康检查通过，记录于 `data/acceptance/live_retrieval.json`。这不代表客户资料的检索质量已通过。验证结束后已停止本轮启动的 retrieval 和 opensearch 容器，恢复它们原先的停止状态。

2026-10-03 隔离恢复报告验证了业务数据库与权限内容哈希一致（包含 5 条 Agent 动作台账），以及 3 份生成材料在新桶中恢复后哈希一致。还原库使用 Asia/Shanghai 时区，源库使用 UTC，规范化核验通过。合成数据库还原耗时约 2.234 秒；`rpo_rto_verified` 和 `production_acceptance` 均为 false。

回归验证：完整后端测试 377 通过、4 项按开关跳过；前端 typecheck 和生产构建通过；Ruff 全库检查、Git diff 检查及新增安全/追踪相关 5 个模块的 mypy 检查通过。全后端 mypy 仍有 5 个未修改文件中的 21 项既有类型错误，不能宣称全库类型检查通过。

另外启用 Chrome 的 4 项浏览器测试，最终全部通过，覆盖交付审批/80 人导入、注册与公司初始化、Agent 与聊天页面。交付测试也验证执行材料快照摘要、输入详情、节点实际耗时及追踪下载。首次批量运行曾在审批人登录跳转超时，加入登录响应断言后单项复测及最终批量运行通过。运行服务报告的针对性测试 2 项通过。

2026-10-03 对齐复核修复了 Agent 动作重放摘要和上下文排序继续读取实时项目需求的问题，统一读取原始执行快照；补齐 Worker 身份校验、Agent 运行账号保护检查、恢复清单与时区规范化，并扩展真实进程恢复演练。新增用例先复现需求变化/授权撤销问题，修复后相关 45 项通过；审批、交付、状态机、邮件/存储与迁移另外 25 项通过。补加个人空间类型/所有权验证后 Worker 9 项复测通过，本次共覆盖 71 个不同测试用例。Ruff 全库、Git diff 检查及本次涉及的 5 个 Agent/Worker/安全追踪模块 mypy 检查通过。本次没有重新运行完整后端或浏览器套件；上面的 377/4 与浏览器结果为 2026-10-02 记录。

正式上线仍需：代表性客户资料的独立检索与回答评分、完整入口权限/提示注入审计、所有服务角色矩阵、厂商邮件投递/退信、持续负载、索引重建与授权核验、告警送达、容量与保留策略、实际服务整体 RPO/RTO，以及业务负责人签署。合成空库的数秒还原不是客户生产库的 RTO。

部署前执行 Alembic upgrade head，再启动 API/Worker。新代码会拒绝缺少新表或不满足角色检查的 PostgreSQL 环境；请使用独立迁移 owner 和应用角色。0030 有快照数据后禁止直接降级删除证据，应按已验证备份在隔离环境恢复；本轮没有迁移正常业务数据库。

<a id="holdout-v1-results"></a>

## 历史留出集首轮结果

> 历史数据集已暴露并用于分析，不能重新当作未见盲测集。86.7% 等数字不能代替当前版本的质量验收。

### 中文摘要

这次测试使用 5 份全新编写且未参与开发调试的 SaaS 实施文档，在首次查询前冻结 30 道题、60 个必要证据片段及全部文件哈希。首次盲测的候选召回率为 100%，但最终证据覆盖率只有 86.7%；25 道可回答问题中 22 道完整命中，5 道无答案问题中只有 3 道被正确拒答。英文提问、中文资料这一跨语言场景的最终证据覆盖率仅为 61.9%。

因此，旧开发集上的 `318/318` 不能作为泛化能力证明。当前系统的主要问题不是初始召回，而是最终相关性筛选会丢弃已召回的跨语言正确证据，同时无答案判断会把语义相近但不包含答案的段落当作证据。本留出集已经暴露结果，不应再用于调参；后续改进应在独立开发集上完成，再使用新建且重新冻结的 `holdout_v2` 复测。

### Protocol

The corpus and labels were created after the original 192-case development corpus had already been used for pipeline iteration. Before the first retrieval query:

- `cases.jsonl` was frozen at SHA-256 `e953b32dd37350f670e9f453df5bc9b52392da4fbd4c35ce8324b0faaa7099ed`.
- All five source hashes were recorded in `sources.json`.
- All labelled spans were checked for literal presence in their source document.
- No retrieval code, model settings, labels or source content were changed after the first result was observed.

The first command attempt failed before database connection because the Windows Proactor event loop is incompatible with async psycopg. It produced no index or query result. The same frozen test was then run with a Selector event loop.

### Dataset

| Item | Count |
| --- | ---: |
| New SaaS implementation documents | 5 |
| Questions | 30 |
| Answerable questions | 25 |
| No-answer questions | 5 |
| Chinese questions | 20 |
| English questions over Chinese sources | 10 |
| Required source spans | 60 |

The cases cover direct facts, multi-span answers, enumerations, conditions, exceptions, contrasts, negative answers and cross-language retrieval. Labels remain agent-authored and are not independently reviewed.

### First-run result

Model profile: BGE-small-zh-v1.5 embeddings with BGE-reranker-base, parser/pipeline 5, rules relevance mode, 1200-token evidence budget.

| Metric | First blind run |
| --- | ---: |
| Candidate required-span recall | 60/60, **100%** |
| Final selected-span coverage | 52/60, **86.7%** |
| Fully covered answerable questions | 22/25, **88.0%** |
| Partially covered answerable questions | 0/25 |
| Zero-coverage answerable questions | 3/25, **12.0%** |
| No-answer questions correctly returning no evidence | 3/5, **60.0%** |
| No-answer false positives | 2/5, **40.0%** |
| Permission leaks | 0 observed |
| Citation integrity errors | 0 observed |
| Evidence budget violations | 0 observed |
| Warm query P95 | **5353.85 ms** |

#### Language split

| Language | Required spans | Candidate recall | Final coverage | Zero-coverage answerable questions |
| --- | ---: | ---: | ---: | ---: |
| Chinese | 39 | 100% | **100%** | 0 |
| English over Chinese sources | 21 | 100% | **61.9%** | 3 |

### Recorded failures

#### Relevant candidates rejected by final selection

- `holdout-05`: asked in English for the required digest format. The required Aurora span was present among candidates, but no evidence passed final relevance selection.
- `holdout-26`: asked in English for four P1 support targets. The exact Ember paragraph ranked as the first candidate, but final selection returned no evidence.
- `holdout-29`: asked in English for enhanced-support exit conditions. The exact Ember paragraph ranked as the first candidate, but final selection returned no evidence.

All three failures had complete candidate recall and zero final coverage. This isolates the main observed weakness to cross-language reranking/relevance filtering rather than initial retrieval.

#### No-answer false positives

- `holdout-12`: a question asking for a hardware security-key brand returned a general emergency-access policy that contained no brand.
- `holdout-18`: a question asking for an object-storage region returned migration checksum and retention rules that contained no region.

These cases show that semantically adjacent passages can still be returned when the requested attribute is absent.

### Interpretation

The old `318/318` development-corpus result did not generalize to this first unseen corpus. Initial retrieval remained broad enough to recall every labelled span, but final evidence selection lost eight required spans and produced two unsupported no-answer matches.

This result must not be used to tune the frozen holdout. Improvements should be developed against separate documents and cases. A future measurement should use a newly authored `holdout_v2`, ideally with independent human labels, longer documents, multiple formats and real anonymized customer material.

The raw immutable first-run output is stored in `first_run.json`. This evaluation measures evidence retrieval, not generated-answer correctness.

<a id="business-results-20260928"></a>

## 2026-09-27 至 28 开发题库对比

> 历史开发集观察：这些文档和问题参与了调试，不属于当前版本的独立验证集。

### 范围与结果

2026-09-27 至 2026-09-28，在本机 Docker 的真实 BGE 模型上，用固定的五份来源、29 道开发题和 1200 token 证据预算比较 pipeline 4 与 5。四份来源是仓库文档快照，另一份是 NIST IR 8397 的 33 页公开 PDF。模型权重、资料、问题和事实标签保持一致。

| 指标 | Pipeline 4 | Pipeline 5 实际 Docker |
| --- | ---: | ---: |
| 候选必要片段召回 | 44/44（100%） | **44/44（100%）** |
| 最终必要片段覆盖 | 40/44（90.9%） | **44/44（100%）** |
| Markdown 最终覆盖 | 35/36（97.2%） | **36/36（100%）** |
| PDF 最终覆盖 | 5/8（62.5%） | **8/8（100%）** |
| 无答案题返回证据 | 0/5 | 0/5 |
| 原文/位置引用错误 | 0 | 0 |
| 范围与预算违规 | 0 | 0 |
| 查询热运行 P95 | 11,298 ms | 22,067 ms |

这衡量的是指定原文片段是否进入最终证据，不是生成回答正确率或语义忠实度。零违规只适用于本轮样本。pipeline 5 消除了本题库的全部已知覆盖缺口，但 P95 增加约 95.3%；标签由代理起草，尚未独立复核，也未完成真实客户资料的锁定验证。因此正式质量验收和校准策略发布仍为关闭状态。

逐题结果、权重版本、代码与资料哈希见 [pipeline 5 比较](../evaluations/retrieval/business/comparison-v5.json)，原文和问题见 [questions.md](../evaluations/retrieval/business/questions.md)。原始诊断保存在本机忽略目录 `data/evaluations/business-deployed/` 和 `business-pipeline5/`。

### 本次修复

- PDF 根据原文中的明确章节、列表标记和空行拆分，避免多个章节被当作整页的必要依赖。保留原文切片、文件页序和提取文字的字符范围；跨页不拼接。
- 短字段在同一结构范围内存在唯一明确对象编号时，重排补充编号原文；竖排两列表格只在字段名和值的结构明确时建立关系。多个编号、不同章节/页/工作表和普通数据表不会推断关联。
- 最终字段证据必须同时保留编号的原文引用，依赖包不能被预算拆开。内部重排上下文不会作为新的引用正文输出。
- 解析子进程固定导入当前代码副本，JSON 字节明确使用 UTF-8，消除工作目录和 Windows 控制台编码影响。
- 评测使用未提交的临时租户，结束后回滚 SQL、删除对应搜索来源并核对清理；不向用户知识库写入这些资料。

### 尚未完整覆盖的三道题

| 题目 | 覆盖 | 诊断与下一步 |
| --- | ---: | --- |
| business-13：相同 key 的私有记忆作用域优先级 | 0/1 | 正确段落已召回，重排分数约 0.142，被当前 0.5 规则阈值过滤。需要独立标注后的相关性校准，以及混合中文/英文术语评测。 |
| business-21：自动验证的静态分析与结果检查 | 1/2 | 找到静态分析要求，另一个独立列表项未入最终证据。需要保留列表引导语的上下文，并评测多事实问题的分解与覆盖。 |
| business-22：中文询问英文文档中的硬编码秘密 | 0/2 | 正确原文在 PDF 第 14 页；候选包含它，但最终选中无关英文段落。需要改进跨语言重排并检查证据精度，不能靠放宽阈值宣布通过。 |

这些题保留原标签和诊断，不通过删题、改页码或缩减必要事实提高分数。PDF 第 12/13/14 页均指文件物理页序，分别对应正文印刷页 5/6/7。

#### Pipeline 5 的处理与实测

2026-09-28 的 pipeline 5 增加明确顺序识别、PDF 同组列表补全、精确对象范围和受限的中英检索术语扩展。完成 Docker 发布、46 份文档重新索引和真实 BGE 复测后，business-13、21、22 分别达到 1/1、2/2、2/2；29 道题中 24 道可回答题全部完整覆盖，5 道无答案题全部正确不返回证据。

### 本机部署与回归

- 执行 `scripts/deploy_local.ps1`：暂停写入、custom-format 备份、临时库恢复校验、迁移检查和全部相关服务重建，包含 retrieval。
- 本次验证备份：`backups/saas_chat_rag_20260913-20260928-130224.dump`，2,742,334 字节。备份未纳入版本控制。
- API `/ready` 为 `0029_task_review_workflow`；真实模型就绪、512 维 CPU 配置和固定权重版本确认。两个消费者分别监听 `celery` 与 `indexing`，beat 为单实例。
- 46 份持久化知识资料全部 ready 且索引版本兼容，没有 pending/failed；已部署的 29 题与 192 题复测均为 pipeline 5 的真实模型结果。
- RAG 相关回归 94 项通过；全量后端 354 项通过、4 项跳过。Ruff、PowerShell 语法、Vue 类型检查和前端生产构建通过。

### 八格式合成边界回归

同一份 192 题、318 个必要片段、相同模型与 1200 token 预算，在 pipeline 5 的实际 Docker 上完整运行。题库哈希保持一致。

| 格式 | Pipeline 4 最终覆盖 | Pipeline 5 最终覆盖 |
| --- | ---: | ---: |
| Markdown | 61.9% | **42/42（100%）** |
| TXT | 95.2% | **42/42（100%）** |
| DOCX | 61.9% | **42/42（100%）** |
| CSV | 42.9% | **42/42（100%）** |
| JSON | 52.4% | **42/42（100%）** |
| PDF | 72.2% | **36/36（100%）** |
| PPTX | 83.3% | **36/36（100%）** |
| XLSX | 27.8% | **36/36（100%）** |
| 总计 | 198/318（62.3%） | **318/318（100%）** |

候选必要片段召回与最终覆盖均为 100%；48 道无答案题均未返回证据，引用、范围和预算检查均为零违规。热运行 P95 为 874 ms，八种格式全部达到逐格式 90% 的自动化覆盖目标。正式门禁仍需独立复核、客户资料锁定验证和生成回答评分。

概要、逐格式和校准/验证域分别统计见 [pipeline 5 边界观测](../evaluations/retrieval/local_context5_observations.json)，原始诊断位于 `data/evaluations/context5/boundary-1200.json`。这些是未独立复核的操作观测，不能替代正式锁定验证。

### 重现与后续

```powershell
pwsh -File scripts/record_local_v3_business.ps1 -OutputDirectory data/evaluations/business-run
.venv/Scripts/python.exe scripts/summarize_v3_business_comparison.py --before data/evaluations/business-baseline/report.json --after data/evaluations/business-run/report.json --output evaluations/v3/business/comparison.json
```

按上述三类失误建立下一批开发题；改进后用独立冻结的题集评测。后续还需要客户业务资料、长文、多页比较、扫描 PDF 和不同权限范围的来源标注，以及生成回答的逐句事实支持与引用检查。独立复核通过前，不改变正式验收标志或开启校准策略。

<a id="local-acceptance-20260928"></a>

## 2026-09-28 本机技术验收

> 历史本机验收：下文的镜像、迁移、测试数量和部署状态对应当时版本，不描述当前运行环境。

范围为本机 Docker 和可重复的发布流程。**pipeline 5 本机技术验收已经通过**；正式检索质量验收尚未完成，`status.json` 中正式验收与校准策略发布标志仍为 `false`。

首轮来源题库、解析/对象上下文改进与最新 Docker 发布见 [检索质量记录](evaluation_report.md#business-results-20260928)，当前运行时状态见 [最新检查](../evaluations/retrieval/report_runtime_latest.json)。本页后半部分保留较早版本观测，供比较和复现使用。

### Pipeline 5 本机技术验收

- Docker Desktop 4.92.0 / Engine 29.8.0 正常；API `/ready` 返回 `0029_task_review_workflow`，Web 返回 200，retrieval 报告真实模型已验证且队列深度为 0。
- 发布脚本完成暂停写入、custom-format 备份、临时数据库恢复核对、迁移检查、所有相关镜像重建和索引回填。验证备份为 `backups/saas_chat_rag_20260913-20260928-130224.dump`，2,742,334 字节。
- 46/46 份 V3 检索文档使用当前 identity 达到 ready，failed 为 0。worker 和 indexer 分别消费 `celery`、`indexing`，beat 为单实例。
- 29 道来源文档题的 44/44 个必要片段均进入最终证据；5 个无答案题均未返回证据。上一版本不完整的 business-13、21、22 已全部覆盖。
- 192 道八格式边界题的 318/318 个必要片段候选召回和最终覆盖均为 100%；MD、TXT、DOCX、CSV、JSON、PDF、PPTX、XLSX 每种格式均为 100%。48 个无答案题均未返回证据。
- 两组评测均未发现权限泄漏、引用错误或预算违规。PDF 边界集为 36/36，确认文字型 PDF 已进入真实 RAG 流程。
- 仓库验证为 94 项相关回归通过、全量后端 354 通过/4 跳过、Ruff、PowerShell 语法、Vue 类型检查和生产构建通过。

这些自动化结果足以签署**本机部署与功能技术验收**。正式质量发布仍需要独立人员复核标签与证据、使用代表性客户资料建立锁定验证集、对 pipeline 5 做成对模型比较，并测量生成回答的忠实度、矛盾率、完整性和真实无答案行为。

### 本次完成

- 恢复 PostgreSQL、Redis、OpenSearch、模型、API、Web、worker、indexer 和单实例 beat。API `/ready` 返回仓库 head `0029_task_review_workflow`；模型就绪并核对固定权重版本。两个 Celery 节点分别消费 `celery` 和 `indexing`。
- 发布执行暂停写入、custom-format 备份、临时库恢复校验、迁移与镜像重建。最终备份为本机 `backups/saas_chat_rag_20260913-20260927-172636.dump`，2,738,321 字节。原有 46 份 V3 文档均为 ready，没有 pending 或失败积压。
- 修复候选 BGE-M3 的下载：固定 commit，校验元信息与权重文件，优先 safetensors，并支持该固定版本的 PyTorch BIN。实际下载、预热和 1024 维索引查询完成。
- 增加两个模型在同一本机上顺序观测的脚本；失败时停止候选并恢复原模型。实际比较后已恢复原模型，候选容器已移除，保留缓存权重。
- 修复聊天页导航栏换行时页面溢出；补回助手中的创建项目入口。更新附件引用、分作用域记忆、历史、归档恢复、删除及返回项目聊天的浏览器验收。
- CI 增加独立浏览器任务，使用前端构建产物并顺序执行四项测试；增加模型比较失败恢复和评测脚本语法检查。远程 CI 尚未运行。

### 已验证

| 检查 | 结果与范围 |
| --- | --- |
| 浏览器回归 | 4 项通过，74.72 秒；实施审批与 80 人导入、注册建公司、Agent 恢复、聊天完整操作 |
| 用户页面要求 | 项目列表移除文档/任务入口；详情标签刷新、分享 URL 保留；RAG 无旧版选择和预算版本标识 |
| 聊天记忆 | context on、历史、分作用域与候选功能下保存/刷新正常；候选提取默认未勾选 |
| 前端 | 锁文件安装、类型检查与构建通过 |
| 评测与下载回归 | 14 项通过，19.18 秒；原文/位置与完整语句核对、预算与无答案、清理失败、权重格式选择 |
| Python 静态检查 | Ruff 对 backend、tests 和本次三个 Python 工具通过 |
| 本机 Docker 页面 | 已部署 JS/CSS 内容哈希与最新构建一致；详情标签刷新、RAG 控件、1000×600 与 390×844 聊天布局通过 |
| 故障恢复模拟 | 备份失败恢复服务；迁移失败恢复已验证备份；候选模型预热失败恢复原模型，均通过 |

浏览器回归使用隔离 API、SQLite 和确定性模型。本机 Docker 页面检查另行使用实际容器。此前完整 Python 回归为 326 通过、4 项浏览器跳过；本次已实际执行这些浏览器测试，新增评测与下载测试另行执行，不能把不同环境的数量合并成一次全套结果。

### 真实模型观测

共 504 次查询观测：40 题 × 三种预算，加上两个模型各 192 道八格式合成边界题。以下为自动标注语句覆盖检查，尚未经独立人工确认。

| 数据与模型 | 预算 | 候选必要语句召回 | 最终必要语句覆盖 | 查询热运行 P95 |
| --- | --- | --- | --- | --- |
| XL-107 演示资料，现有模型 | 600 | 100% | 94.4% | 14,061 ms |
| XL-107 演示资料，现有模型 | 1200 | 100% | 100% | 16,182 ms |
| XL-107 演示资料，现有模型 | 1800 | 100% | 100% | 10,756 ms |
| 八格式边界题，现有模型 | 1200 | 99.4% | 21.1% | 2,931 ms |
| 同一边界题，多语言候选模型 | 1200 | 100% | 30.8% | 14,007 ms |

现有模型八种格式均未达到 90% 覆盖要求；候选仅 JSON 达到。候选耗时更长，也未通过整体质量要求，未自动切换业务模型。短语句的对象上下文与最终相关性过滤需要进一步审查，不能仅依据候选召回率宣布完成。

上述观测未发现范围、引用原文/位置或证据预算违规，不能替代全面的权限证明。演示题有一条“无答案”标注返回了明确说明真实密钥不在资料中的语句，需要复核答案定义；没有将此差异隐藏或改写为通过。

摘要见 [演示观测](../evaluations/retrieval/local_demo_observations.json)、[边界观测](../evaluations/retrieval/local_boundary_observations.json) 和 [模型比较](../evaluations/retrieval/local_model_comparison.json)。原始诊断保存在本机忽略目录 `data/evaluations/`，没有提交账号凭据、模型权重或数据库备份。评测的临时 SQL 已回滚，生成的 OpenSearch 来源已清理并核对。

### 未完成的验收与下一步

1. 补充脱敏业务资料与独立复核人，覆盖长文档、多页与扫描 PDF、跨文档比较、权限和无答案情形；核对题目、必要事实、条件与原文位置。按 [复核清单](testing.md#review-checklist) 记录审核结果。
2. 在独立审核的校准集上改进对象上下文和相关性判定；训练或校准策略。冻结解析器、模型配置、分类器与题库哈希后再执行验证，避免用验证题反复调参。
3. 补齐成对旧流程基线。二进制文档采用同内容且授权一致的 MD/TXT 参考，单独说明比较口径。
4. 执行锁定验证及全部八格式的完整性、权限、引用、预算与热运行延迟门禁。通过独立复核后才启用校准策略；失败则保留诊断与数据。

这些事项尚有真实资料与人工复核依赖。当前成功部署使用 pipeline 5 的 V3 rules 策略，Docker 与业务检索服务已就绪。pipeline 5 已发布到本机业务流程；候选模型和校准策略仍未获准切换。

<a id="retrieval-report-20260928"></a>

## 2026-09-28 检索详细测评

> 历史报告：保留版本、统计与原始证据来源；不得把当时的满分或质量缺项直接应用于当前代码。

**报告日期**：2026-09-28，Asia/Shanghai。
**测评对象**：企业与项目知识文档的 V3 检索、文档解析、证据筛选与引用，以及相关聊天/Agent 功能回归和本机 Docker 发布记录。
**最新测量**：解析器/证据流程 pipeline 5，相关性模式 `rules`，真实 BGE 模型。
**测量环境**：Windows 本机 Docker Desktop 4.92.0、Engine 29.8.0、CPU 推理。
**数据量**：261 道不同问题、975 次主要有效查询观测；pipeline 5 最新观测为 221 次，来自 29 道来源文档题和 192 道合成边界题。
**数据标注**：代理起草，尚未独立人工复核。不是经审核的客户业务 golden set。
**原始明细**：本机 `data/evaluations/`，包含候选、最终证据、来源位置、预算和耗时；版本化摘要见本报告链接与 [补充统计 JSON](../evaluations/retrieval/detailed_report_metrics.json)。

### 结论

pipeline 5 的五份来源文档达到 **44/44（100%）** 必要片段最终覆盖，其中 PDF 为 **8/8（100%）**；八格式合成边界题达到 **318/318（100%）**，八种格式均达到 100%。53 道无答案题均未返回证据，自动检查未发现权限泄漏、引用错误或预算违规。这支持本机部署与功能技术验收通过。

聊天、Agent、引用和页面已有功能回归记录；生成回答的忠实度、人工质量评分、标准 Recall@3/5、MRR 和 nDCG 尚未完成。**检索覆盖率不能作为生成回答正确率。**

2026-09-28 13:24 的发布与复查确认 API、Web 与真实模型就绪，迁移为 0029，两个队列消费者与单实例 beat 在线，46/46 份 V3 文档使用当前 identity 达到 ready。发布前备份已在临时数据库恢复核对。正式检索质量验收和校准策略发布标志仍为 `false`，原因是独立人工复核、客户资料锁定验证、pipeline 5 成对模型比较和生成回答质量评分尚未完成。

### 2026-09-28 Pipeline 5 部署与实测状态

- 精确对象编号会先限定已授权来源文档，再在同一对象范围内补取明确字段标签；未知字段不会用相邻内容替代。
- PDF 项目符号保留为同一列表范围，多事实枚举问题可补全同组条目；解析器 schema 升至 5，部署后必须重新索引。
- 优先级问题只接受原文中明确的 `A > B > C` 顺序；中问英答只对显式触发的受限术语做检索扩展，并要求来源逐项出现。
- 发布脚本现在等待全部 V3 文档达到当前索引 identity，发现失败文档或 15 分钟仍未同步会终止发布，不再提前报告成功。
- RAG、解析、策略、适配器、评测和队列相关回归 **94 项通过**；全量后端测试 **354 项通过、4 项跳过、0 项失败**。全仓 Ruff、PowerShell 语法、Git diff 检查、Vue 类型检查和前端生产构建通过。
- 已按“部署 → 46 份资料回填 → 29 题来源集 → 192 题八格式集”完成真实模型测量。运行状态见 [最新运行时检查](../evaluations/retrieval/report_runtime_latest.json)，pipeline 5 边界摘要见 [多格式观测](../evaluations/retrieval/local_context5_observations.json)。

### 汇总表

| 维度 | 指标 | 改进前 → 最新有效测量 | 数据与结论 |
| --- | --- | --- | --- |
| 来源文档检索 | 候选必要片段召回 | 44/44 → **44/44，100%** | 24 道可回答题；所有候选诊断的覆盖，非 Recall@k |
| 来源文档检索 | 最终必要片段覆盖 | 40/44，90.9% → **44/44，100%** | 29 次 pipeline 5 查询；PDF 8/8 |
| 来源文档检索 | 可回答题完整覆盖 | 21/24，87.5% → **24/24，100%** | 5 道无答案题另行正确拒答 |
| 八格式边界检索 | 候选必要片段召回 | 316/318，99.4% → **318/318，100%** | 144 道可回答题 |
| 八格式边界检索 | 最终必要片段覆盖 | 198/318，62.3% → **318/318，100%** | 八种格式均为 100% |
| 八格式边界检索 | 可回答题完整覆盖 | 100/144，69.4% → **144/144，100%** | 48 道无答案题另行正确拒答 |
| 无答案处理 | 无答案题返回证据 | 最新来源题 **0/5**；最新边界题 **0/48** | 历史演示题另有争议，不并入“全程零误报” |
| 引用与授权 | 原文/位置错误、范围泄漏 | 最新两组均 **0** | 本轮自动核对；不等于完整安全认证 |
| 证据预算 | 超出 1200 token | 最新两组均 **0** | BGE 检索证据 tokenizer 口径 |
| 性能：来源题 | 热查询 P95 | 11.298s → **22.067s** | 29 次；覆盖提升但延迟回退，需继续优化 |
| 性能：边界题 | 热查询 P95 | 3.461s → **0.874s** | 192 次；不含生成、解析和入库 |
| 生成忠实度 | 均分、矛盾/无支持事实率 | **未测** | 未保存并判分真实模型最终回答 |
| 回答质量 | 多维评分与达标率 | **未测** | 功能回归不是回答质量评分 |
| 排名质量 | Recall@3/5、MRR、nDCG@5 | **未测** | 缺少完整相关片段与分级相关性标注 |
| 意图路由 | 标注查询路由准确率 | **未测** | 尚无独立路由测评集 |
| 性能：聊天/Agent | 全链路延迟、完整 token/请求 | **未测** | 不能使用检索耗时或证据 token 代替 |
| 部署 | 当前本机运行状态 | **本机技术验收通过** | API、Web、模型、队列、备份恢复与 46/46 索引正常 |

### 1. 测评数据、模型与方法

#### 1.1 三类问题和观测数量

| 数据集 | 不同问题数 | 已计入的查询次数 | 用途 |
| --- | ---: | ---: | --- |
| XL-107 演示资料 | 40 | 120 | 同一模型、历史版本 3，预算 600/1200/1800 各一次 |
| 八格式合成边界题 | 192 | 768 | 版本 3 原模型、版本 3 多语言候选、pipeline 4、pipeline 5 各 192 次 |
| 固定来源文档开发题 | 29 | 87 | 同资料/问题/预算/权重，三个有效版本各 29 次 |
| 合计 | **261** | **975** | 重复预算和重复版本不会被算成新的问题 |

当前 pipeline 5 的两组主要结果共 221 次查询、168 道可回答题、53 道无答案题、362 个必要原文片段。两组难度和资料规模不同，分别汇报。有效预演与部署复测存在重复，预演不再次计入 975 次；一次混用新旧解析代码的预演为无效记录，也未计入。

#### 1.2 来源文档集

来源为四份项目维护文档固定快照和一份公开 PDF：

| 来源 | 内容 | 说明 |
| --- | --- | --- |
| `RAG_BINARY_DOCUMENTS.md` | 二进制文档、解析和索引规则 | 仓库快照 |
| `CONTEXT_MEMORY.md` | 聊天上下文、历史与作用域记忆 | 仓库快照 |
| `CHAT_AGENT.md` | 聊天、Agent 与引用路径 | 仓库快照 |
| `PREPRODUCTION_RUNBOOK.md` | 预发布、备份与恢复流程 | 仓库快照 |
| `NIST.IR.8397.pdf` | 软件验证要求 | 公开原文，33 个文件物理页；本轮未触发 OCR |

四份仓库快照源版本为 `306ef05cc14b0ffe75cb811884dbffabcac225b2`。资料和题库的 SHA256 见 [来源登记](../evaluations/retrieval/business/sources.json)、[比较记录](../evaluations/retrieval/business/comparison.json)；题目、必要事实和 PDF 物理页见 [逐题标注](../evaluations/retrieval/business/questions.md)。这些是真实存在的资料，尚不是客户业务资料。

#### 1.3 八格式边界题

每种格式 24 题，含 18 道可回答题和 6 道无答案题；覆盖 MD、TXT、DOCX、CSV、JSON、PDF、PPTX、XLSX，以及事实、枚举和限制条件问题。校准域为库存、教育、设施；验证域为出版、活动、软件，领域不重合，各 96 题。

pipeline 5 代码在完整测量前冻结，没有依据本轮验证域结果再次修改代码。不过标签尚未独立复核，资料主要为短合成样本，因此“验证域观测”仍不等于正式批准的锁定验证。扫描件、复杂跨页表格、长文、多来源冲突和真实业务表达的覆盖不足。

#### 1.4 被测模型及策略

| 项目 | 主要测量配置 |
| --- | --- |
| Embedding | `BAAI/bge-small-zh-v1.5`，512 维，最大长度 512 |
| Embedding 权重 revision | `7999e1d3359715c523056ef9478215996d62a620` |
| Reranker | `BAAI/bge-reranker-base`，最大长度 256、最多 32 个窗口 |
| Reranker 权重 revision | `2cfc18c9415c912f9d8155881c133215df768a70` |
| 相关性 | `rules`；重排筛选阈值 0.5 |
| 版本 | 特征 schema 2；最新解析/证据流程 schema 5 |
| 证据预算 | 最新两组均为 1200 个检索 tokenizer token |

阈值 0.5 是当前启发式筛选配置，不表示“50% 正确概率”。历史就绪时的应用配置为 `MODEL_MODE=real`、`MODEL_NAME=qwen-plus`、`AGENT_ENGINE=legacy`、`RAG_MODE=real`。本报告的检索数字来自真实 BGE 服务，**没有对 qwen-plus 最终生成回答进行质量判分**，也不将其他 Agent 引擎的测试结果当作当前引擎已部署。

#### 1.5 判分口径

- **候选必要片段召回**：候选诊断包含的标注必要原文数 ÷ 全部必要原文数。统计的是候选池，不限制 top-3 或 top-5。
- **最终必要片段覆盖**：最终选中证据包含的标注必要原文数 ÷ 全部必要原文数。要求对应来源、字段/位置和保护条件，PDF 还要求标注物理页一致。
- **逐题完整覆盖**：可回答题的全部必要原文都进入最终证据。无答案题不计入此分母。
- **引用检查**：证据正文与原始文本及位置元数据匹配；空白可规范化，值、否定和条件不能被替换或丢失。
- **无答案观测**：标注为无答案的题是否仍返回证据。这不是自由生成模式下的拒答/幻觉评分。
- **热查询延迟**：索引完成、模型已预热后，查询和证据组装的时间；P95 使用最近秩 `ceil(0.95 × n)`，不包含上传、解析、入库或生成。

### 2. RAG 检索质量

#### 2.1 来源文档：29 次查询、44 个必要片段

| 指标 | Pipeline 4 | 实际部署 Pipeline 5 |
| --- | ---: | ---: |
| 候选召回 | 44/44，100% | **44/44，100%** |
| 最终覆盖 | 40/44，90.9% | **44/44，100%** |
| Markdown 覆盖 | 35/36，97.2% | **36/36，100%** |
| PDF 覆盖 | 5/8，62.5% | **8/8，100%** |
| 可回答题完整覆盖 | 21/24，87.5% | **24/24，100%** |
| 可回答题部分覆盖 | 1 | **0** |
| 可回答题零必要事实覆盖 | 2 | **0** |
| 无答案题返回证据 | 0/5 | **0/5** |

pipeline 5 消除了本开发题库中已知的最终证据缺口，PDF 和跨语言问题均达到完整覆盖。来源题热查询 P95 从 11.298s 增至 22.067s，质量提升伴随明显延迟回退，仍需单独优化和做并发负载测试。

#### 2.2 八格式边界：192 次查询、318 个必要片段

| 格式 | 必要片段数 | Pipeline 5 候选召回 | Pipeline 4 最终覆盖 | Pipeline 5 最终覆盖 | 最新热查询 P95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| MD | 42 | 42/42，100% | 26/42，61.9% | **42/42，100%** | 0.721s |
| TXT | 42 | 42/42，100% | 40/42，95.2% | **42/42，100%** | 0.629s |
| DOCX | 42 | 42/42，100% | 26/42，61.9% | **42/42，100%** | 0.696s |
| CSV | 42 | 42/42，100% | 18/42，42.9% | **42/42，100%** | 0.303s |
| JSON | 42 | 42/42，100% | 22/42，52.4% | **42/42，100%** | 0.931s |
| PDF | 36 | 36/36，100% | 26/36，72.2% | **36/36，100%** | 0.322s |
| PPTX | 36 | 36/36，100% | 30/36，83.3% | **36/36，100%** | 0.513s |
| XLSX | 36 | 36/36，100% | 10/36，27.8% | **36/36，100%** | 0.874s |
| 合计 | 318 | **318/318，100%** | 198/318，62.3% | **318/318，100%** | **0.874s** |

八种格式在该合成边界集上全部达到 100%，同时保持无答案、引用、范围和预算检查为零违规。结果证明 pipeline 5 已修复此前的字段枚举与结构证据缺口；由于题库是合成样本且标签未独立复核，不能把它推广为所有真实文档均为 100%。

#### 2.3 按题型分析

| 题型 | 题数 | 最新候选必要片段 | 最新最终必要片段 | 最新逐题完整覆盖 |
| --- | ---: | ---: | ---: | ---: |
| 单事实 | 48 | 48/48，100% | **48/48，100%** | 48/48，100% |
| 字段枚举 | 48 | 192/192，100% | **192/192，100%** | 48/48，100% |
| 条件/限制 | 48 | 78/78，100% | **78/78，100%** | 48/48，100% |
| 无答案 | 48 | 不适用 | 不适用 | 0/48 返回证据 |

本轮单事实、枚举和条件题均完整覆盖。后续锁定验证要增加长文、多页表格、扫描 PDF、跨文档冲突和真实业务表达，避免只在当前模板分布上评估。

#### 2.4 校准域与验证域

| 域 | 查询数 | 候选必要片段召回 | 最终必要片段覆盖 | 无答案题返回证据 | 热查询 P95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 校准域 | 96 | 159/159，100% | **159/159，100%** | 0/24 | 未单列 |
| 验证域 | 96 | 159/159，100% | **159/159，100%** | 0/24 | 未单列 |

校准域和验证域在当前固定边界题上都完整覆盖。由于代码曾依据同一类合成题的历史失败改进，正式发布仍需使用未参与开发且经独立复核的客户资料锁定集。

#### 2.5 标准排名指标的缺口

目前没有可发布的 Recall@3/5、MRR、nDCG@5。现有标签规定“哪些事实必须保留”，没有完整标注各候选片段的相关性等级；候选诊断还包含补充召回和未入重排的材料，直接截取前 k 项会改变指标含义。

后续需要冻结用于排名判分的 chunk 列表、查询范围和去重规则，人工标注完整相关集合及相关性等级，再计算标准指标。不能把 99.4% 候选片段召回重新命名为 Recall@5，也不能使用与实际相关文档数量不符的 IDCG。

### 3. 生成忠实度与回答质量

| 项目 | 本次状态 | 仍需测量的内容 |
| --- | --- | --- |
| 引用原文/位置准确性 | 最新两组自动检查为 0 错误 | 仍须人工确认语义和复杂结构 |
| 生成忠实度均分 | 未测 | 最终回答逐句是否被选中证据支持 |
| 矛盾率与无支持事实率 | 未测 | 数值、主体、否定、限制条件和跨文档冲突 |
| 回答完整性 | 未测 | 最终回答是否覆盖问题必要要点 |
| 可用性/行动建议 | 未测 | 是否回答问题、建议可执行、描述清晰 |
| 真实模型无答案处理 | 未测 | 没有证据时是否明确说明，不臆造答案 |

正确原文可以被正确引用，却不能回答当前问题。来源题 `business-22` 就出现“最终证据引用合法，但没有覆盖目标事实”的情况，因此引用错误为零不等于回答忠实度或证据精度满分。

下一轮生成测评应保存完整问题、权限范围、最终选中证据、最终回答、模型配置和输入/输出 usage；使用独立判分标准，对忠实度、事实准确性、完整性和表达清晰度分别评分。人工独立复核之前，不发布“4.x/5”“100% 达标”等分数。本报告没有使用同模型自评，也没有宣称完成 20% 人工抽检。

### 4. 聊天与 Agent 功能回归

以下为已有执行记录的整理，本次报告编写没有重新执行整套产品测试。

| 检查批次 | 已记录结果 | 说明 |
| --- | --- | --- |
| 历史 Python 全套 | 326 passed，4 skipped，约 698s | 较早版本；跳过项为浏览器测试，不能代表最新代码全套 |
| 后续隔离浏览器 | 4 passed，74.72s | 审批与导入、注册建公司、Agent 恢复、聊天操作；隔离 API/SQLite/确定性模型 |
| 检索/解析/策略/适配器 | 71 passed，123.79s | 本轮开发期间的相关回归 |
| 聊天/流式/范围/Agent/评测 | 44 passed，129.80s | 本轮相关路径回归 |
| 最后修改后的解析/上下文 | 16 passed，40.39s | 最新针对性检查 |
| 最后修改后的评测工具 | 9 passed，18.89s | 最新针对性检查 |
| Ruff、PowerShell 语法 | 已记录通过 | 静态/语法检查 |
| 前端锁文件安装、类型检查、构建 | 历史已记录通过 | 本次没有修改前端，也未重跑构建 |

71、44、16、9 项包含重叠，不相加成“最新全套 140 项”；历史 326 和浏览器 4 项也不合并成同一次执行。远程 CI 尚无本报告确认的运行结果。

功能记录覆盖：项目文档和协作任务入口位于详情页；详情标签与 URL 同步；RAG 查验移除流程选择和预算版本标识；附件引用；聊天历史、归档恢复、删除、返回项目聊天和作用域记忆；审批、导入与 Agent 状态恢复。记忆候选功能的用户开启选择仍默认未勾选。

这些检查验证流程能运行和权限边界，没有建立真实模型回答质量、真实用户任务完成率或意图路由准确率。相关历史依据见 [本机验收记录](evaluation_report.md#local-acceptance-20260928)。

### 5. 授权、无答案、引用与预算

#### 5.1 最新检索样本

| 检查 | 来源文档 29 次 | 边界题 192 次 |
| --- | ---: | ---: |
| 范围泄漏 | 0 | 0 |
| 引用原文/位置错误 | 0 | 0 |
| 超出证据预算 | 0 | 0 |
| 无答案题返回证据 | 0/5 | 0/48 |
| 临时数据清理 | SQL 回滚、生成搜索来源删除并核对 | SQL 回滚、生成搜索来源删除并核对 |

零事件仅适用于这些样本。租户、项目、权限变化、停用版本等还有专门回归路径，但本次检索集尚不足以提供完整的跨租户攻击、提示注入、聊天附件与交付物联合授权测评；不能表述为“系统安全率 100%”。

#### 5.2 历史演示集的无答案争议

历史版本 3 的 XL-107 演示题，在每种预算下均有 1/4 无答案题返回证据。三种预算合计是 3/12 次观测，对应同一道问题，而不是三道不同失败题。原文明确说明真实密钥未提供，是否应把这类“明确不存在”的答案标注为无答案，需要独立复核。

保留该差异，未删题或修改标签提高结果；该集尚未在版本 4 重跑，因此不把最新边界集的零返回结果推广到所有历史问题。

#### 5.3 状态码与完整性

最新来源题状态：`evidence_found` 18、`budget_limited` 5、`insufficient_relevance` 6。最新边界题状态：40、63、89。`budget_limited` 可以包含必要事实，`evidence_found` 也可能缺少标注事实；这些是诊断状态，不是自动判定的准确率。流程仍报告 `coverage/completeness=unknown`，没有根据状态直接承诺回答完整。

### 6. 模型比较：历史版本 3

同一本机、同一 192 题和 1200 token 预算下，顺序比较了两组真实模型：

| 模型组合 | 候选必要片段召回 | 最终必要片段覆盖 | 热查询 P95 |
| --- | ---: | ---: | ---: |
| BGE-small-zh-v1.5 + reranker-base | 316/318，99.4% | 67/318，21.1% | 2.931s |
| BGE-M3 + reranker-v2-m3 | 318/318，100% | 98/318，30.8% | 14.007s |

多语言候选增加 31 个最终必要片段，P95 约为原模型 4.78 倍；只有 JSON 达到 90%，整体仍未通过。实际比较后恢复原模型，候选容器已移除，缓存权重保留，未自动切换业务模型。

候选权重固定为：Embedding `5617a9f61b028005a4858fdac845db406aefb181`，Reranker `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`；1024 维，重排最大长度 512。依据见 [模型比较 JSON](../evaluations/retrieval/local_model_comparison.json)。

上述比较使用版本 3。不能把历史候选版本 3 的 30.8% 与当前原模型版本 4 的 62.3% 当作同代码的模型胜负；版本 4 仍需独立审核后重新成对测量。

### 7. 性能、token 与文档解析

#### 7.1 热查询延迟

| 数据集与版本 | 次数 | Mean | P50 | P95 | Max |
| --- | ---: | ---: | ---: | ---: | ---: |
| 来源文档，版本 3 | 29 | 7.002s | 5.487s | 14.136s | 15.261s |
| 来源文档，版本 4 | 29 | **6.724s** | **5.872s** | **11.298s** | **15.996s** |
| 来源文档，pipeline 5 | 29 | 8.361s | 5.778s | **22.067s** | 30.706s |
| 边界题，版本 3 | 192 | 1.624s | 1.655s | 2.931s | 3.926s |
| 边界题，版本 4 | 192 | **2.158s** | **2.266s** | **3.461s** | **5.679s** |
| 边界题，pipeline 5 | 192 | **0.485s** | **0.447s** | **0.874s** | **0.932s** |

pipeline 5 来源题 P95 较版本 4 增加约 95.3%，主要风险是较长真实来源查询的尾延迟；边界题 P95 则降至 0.874s。样本数量、文档规模和查询内容不同，两组不直接比较快慢。本轮没有并发吞吐、连续稳定性或统计置信区间，单次顺序观测不足以确定稳定性能收益。

#### 7.2 最终证据 token

| 最新数据集 | 全部查询 Mean / P50 / P95 / Max | 有证据查询 Mean | 预算 |
| --- | --- | ---: | ---: |
| 来源文档 | 355.79 / 252 / 1058 / 1098 | 448.61（23 次） | 1200 |
| 边界题 | 339.73 / 169.5 / 1152 / 1167 | 633.29（103 次） | 1200 |

全部查询均值包含没有证据的零 token 请求，因此另列有证据均值。这是 BGE 检索 tokenizer 对最终证据的计数，**不包含 qwen-plus 输入/输出、聊天历史、系统提示、工具调用或 Agent 多轮总消耗**。

从候选序列化总量到最终证据的过滤缩减，来源题为 96.8%，边界题为 88.2%。该数包含过滤和无答案清空的影响，不能用于宣称实际模型费用节约，也不能替代正式门禁的“与旧流程成对比较、单事实题中位数 token 至少减少 25%”。后一项目前没有完整基线。

#### 7.3 来源文档解析与入库

| 文件 | 解析耗时 | 入库耗时 | 原文节点 / 检索单元 | OCR 页数 |
| --- | ---: | ---: | ---: | ---: |
| RAG_BINARY_DOCUMENTS.md | 29.12ms | 1.194s | 27 / 10 | 0 |
| CONTEXT_MEMORY.md | 5.41ms | 2.987s | 86 / 46 | 0 |
| CHAT_AGENT.md | 3.33ms | 1.991s | 28 / 22 | 0 |
| PREPRODUCTION_RUNBOOK.md | 1.96ms | 2.001s | 30 / 24 | 0 |
| NIST.IR.8397.pdf | 1.333s | 30.650s | 158 / 214 | 0 |

这些是首次准备测试索引的耗时，单列于热查询之外；不包含浏览器上传耗时。PDF 结果证明文字型 PDF 已进入实际 RAG 流程，不能用于声明扫描 PDF/OCR 的质量已验收。旧式 DOC/PPT/XLS 仍需转换，不与 DOCX/PPTX/XLSX 混淆。

#### 7.4 历史预算观测

| XL-107 演示预算 | 必要片段覆盖 | 无答案题返回证据 | 热查询 P95 |
| --- | ---: | ---: | ---: |
| 600 | 51/54，94.4% | 1/4 | 14.061s |
| 1200 | 54/54，100% | 1/4 | 16.182s |
| 1800 | 54/54，100% | 1/4 | 10.756s |

这是版本 3 的小规模顺序观测；延迟并未随预算单调变化，不能只依据此表选择最优预算。版本 4 的预算回归和并发负载仍待补测。

### 8. Docker、数据库和索引验收

#### 8.1 最近已记录的发布成功状态

最近发布使用 `scripts/deploy_local.ps1`，执行暂停写入 → PostgreSQL custom-format 备份 → 临时库恢复验证 → 迁移检查 → 服务重建（含 retrieval）→ 索引版本核对 → 业务检索复测。

| 检查 | 历史发布验收结果 |
| --- | --- |
| 验证备份 | `backups/saas_chat_rag_20260913-20260927-182854.dump`，2,738,262 字节；临时数据库恢复校验通过 |
| API 就绪 | `/ready` 返回 `0029_task_review_workflow` |
| 真实模型 | `/health/ready` 为 ready，real_models_verified，CPU、512 维、固定权重版本 |
| 容器健康 | API、retrieval、PostgreSQL、OpenSearch 等相关服务通过检查 |
| 队列 | worker 消费 `celery`，indexer 消费 `indexing`，beat 单实例 |
| 调度 | API 内置索引调度关闭 |
| 持久化 V3 索引 | 46 份来源 ready，版本兼容，pending/failed 为 0 |
| 实际容器代码 | 八个修改的检索模块内容哈希与工作区一致 |
| 前端资源 | 实际容器 JS/CSS 哈希与构建产物一致 |
| 临时测评租户 | SQL 回滚，搜索生成来源删除并核对 |
| 故障恢复模拟 | 历史备份失败、迁移失败、候选预热失败恢复检查通过 |

前端资源哈希记录：

```text
index-B9Mhu5H2.js  08e245433795b58547adf99bb4f6ce1742e4073486b5cdff390845a042b567f1
index-DDgg5shM.css 85dce73f4228869a4fa144c511d16240a78eb2d81a98e88513fec5af8dd322f6
```

历史检查中聊天上下文为 `on`，历史、分作用域记忆、记忆候选功能配置启用；记忆候选仍需用户自行选择开启。功能标志已经开启不能替代内容质量和真实使用效果的评测。

#### 8.2 本次报告编写时的复查

**复查时间：2026-09-27 18:59，Asia/Shanghai。**

本次只读检查摘要见 [运行状态记录](../evaluations/retrieval/report_runtime_check.json)。

| 命令/接口 | 本次结果 |
| --- | --- |
| `docker context show` | `desktop-linux` |
| `docker ps -a` | 没有任何容器 |
| `docker compose ps` | 没有项目容器 |
| `http://localhost:8000/ready` | 连接被拒绝 |
| `docker compose exec ... api` | `service "api" is not running` |

原因尚未确认；当时的容器列表为空不能用于推断数据库卷、原文文件或备份是否丢失。该时点的检查保留为运行历史，不能被后续恢复结果覆盖或删除。

#### 8.3 Docker 恢复复查

**复查时间：2026-09-27 23:59，Asia/Shanghai。** 恢复摘要见 [运行恢复记录](../evaluations/retrieval/report_runtime_recheck.json)。

| 检查 | 恢复结果 |
| --- | --- |
| Compose 服务 | API、Web、PostgreSQL、Redis、OpenSearch、retrieval、worker、indexer、beat、Prometheus、Grafana 均运行；migrate 正常退出 0 |
| API | `http://localhost:8000/ready` 返回 ready，schema 为 `0029_task_review_workflow` |
| Web | `http://localhost:8080` 返回 HTTP 200 |
| 检索模型 | ready、真实权重已验证、CPU、512 维、队列深度 0；Embedding/Reranker revision 与测评一致 |
| 队列 | `celery` 与 `indexing` 两个消费者在线；beat 实例数 1 |
| 索引 | ready 46，pending/parsing/indexing/failed 均为 0，最旧积压 0 秒 |
| Outbox | pending 0 |
| 功能配置 | qwen-plus real、RAG real、V3 rules；PDF/Office、聊天上下文、历史和作用域记忆启用 |

这证明本机基础运行环境已恢复。它没有重新执行上传、RAG 查验、真实聊天和 Agent 的完整端到端业务用例，也没有改变第 10 节的检索质量门禁结果。

### 9. 评测发现的修复与保留问题

#### 9.1 本轮已实现并测量的改进

1. **PDF 结构拆分**：根据明确章节、列表标记和空行拆分，保留原文切片、物理页及字符位置，不跨页拼接。
2. **短字段对象上下文**：仅在同一结构范围存在唯一明确对象编号时补充原文上下文；明确竖排两列表格允许建立字段关系，普通数据表、多编号或跨范围不推断。
3. **证据依赖保留**：字段证据与对象编号原文一起进入最终证据，预算不能拆开依赖；重排辅助文本不作为新的引用正文输出。
4. **解析子进程一致性**：固定导入当前代码副本，JSON 字节使用 UTF-8，消除工作目录和 Windows 编码影响。
5. **索引版本更新**：解析/证据 schema 升为 4，不兼容旧索引重建；保留 rules 模式，未启用未获批准的校准策略。

一次预演曾出现主流程版本 4、隔离解析子进程版本 3 混用，记录位于 `data/evaluations/business-improved/`，标记 `valid_comparison=false`。修复后重新测量并在实际部署上复测；无效预演未用于提高分数。

#### 9.2 来源文档仍不完整的三道题

| ID | 问题 | 最新覆盖 | 诊断 |
| --- | --- | ---: | --- |
| business-13 | 相同 key 的私有记忆作用域优先级 | 0/1 | 正确段落已召回，重排约 0.142，低于当前 0.5 阈值；混合中英文术语与相关性校准仍需改进 |
| business-21 | 自动验证的静态分析与结果检查 | 1/2 | 静态分析已覆盖，另一独立列表项遗漏；需保留列表引导上下文和多事实覆盖 |
| business-22 | 中文询问英文文档中的硬编码秘密 | 0/2 | 正确段落在 PDF 物理第 14 页，候选包含，最终选中无关英文材料；需跨语言重排与证据精度检查 |

PDF 物理第 12/13/14 页对应正文印刷页 5/6/7。原标签、页码和必要事实保持不变，没有通过删题、缩减事实或放宽命中定义宣布通过。

#### 9.3 后续优先级

| 优先级 | 工作 | 验证方式 |
| --- | --- | --- |
| 已完成 | 恢复本机 Docker 基础运行状态 | API、模型、队列和索引已复查；完整业务冒烟仍纳入最终验收 |
| P1 | 修复字段枚举、XLSX/CSV/JSON 与列表条件遗漏 | 开发题定位失败；独立冻结题集上按格式、题型、条件完整性复测 |
| P1 | 改进跨语言相关性及证据精度 | 中英同内容成对题，相关/无关片段独立标注；不能仅放宽阈值 |
| P1 | 复核数据并补齐正式旧流程基线 | 客户脱敏资料、必要事实和来源位置人工确认；二进制内容有同授权 MD/TXT 对照 |
| P1 | 生成忠实度和回答质量测评 | 保存实际回答和完整证据，逐句支持/矛盾检查、固定评分标准及独立复核 |
| P2 | 复杂 PDF/Office、长文与多来源比较 | 扫描件、OCR、跨页表格、多工作表和冲突题分项报告 |
| P2 | 标准排名指标与路由评测 | 完整相关集合、分级相关性及独立路由标签 |
| P2 | 全链路性能与费用统计 | 并发、失败率、稳定性、聊天/Agent 分节点耗时与实际 usage |

### 10. 正式门禁结果

门禁定义来自 `backend/rag_v3_evaluation.py` 与 [V3 评测流程](testing.md#retrieval-regression)，要求独立审核、成对旧流程与锁定验证，不能只看一项总分。

| 门禁 | 目标 | 当前证据与判定 |
| --- | --- | --- |
| 逐格式候选召回 | ≥95% | 最新八格式观测均达到；**仅样本层面满足** |
| 逐格式最终覆盖 | ≥90%，且不低于成对旧流程 | pipeline 5 八格式均为 100%；**自动化样本通过**，正式客户资料基线仍未补齐 |
| 无答案误返回 | ≤5% | 最新两组为 0；历史演示标签有争议，尚无独立正式判分；**待正式验证** |
| 单事实题 token 中位数 | 比旧流程减少 ≥25% | 缺少正式旧流程数据；**未测** |
| 热查询 P95 | 相对旧流程 ≤1.5 倍 | 与流程版本 3 的本轮对比不同于正式旧流程基线；**待正式验证** |
| 权限、来源、事实、预算 | 零违规 | 最新样本范围/引用/预算自动检查为零；完整人工事实及安全评测未完成 |
| 标注和复核 | 独立审核通过 | 标签为代理草稿；**未完成** |
| 模型比较 | 同代码、同资料及环境，独立批准 | 历史版本 3 已比较；pipeline 5 正式比较与批准未完成 |
| 本机服务就绪 | 数据库、模型、队列和索引可用 | **通过**；备份恢复、API、Web、模型、队列和 46/46 索引均已核对 |

结论：`release_enabled=false`、`quality_evaluation_completed=false`、`independent_review_completed=false`。已部署过的业务策略是 V3 rules，尚未获准发布校准模式。对应事实记录见 [status.json](../evaluations/retrieval/status.json)。

### 11. 复现命令

以下命令在项目根目录执行。运行真实模型评测前必须确认数据库、模型、队列和索引就绪；同一模型服务上顺序运行，避免竞争改变耗时。

#### 11.1 重算本报告补充统计

```powershell
.venv/Scripts/python.exe scripts/summarize_v3_report_metrics.py
```

读取四份已有有效原始报告，生成 [detailed_report_metrics.json](../evaluations/retrieval/detailed_report_metrics.json)。它不调用模型、不改数据库、不改变正式验收标志。原始明细位于忽略目录，其他机器需要取得相同且授权可用的文件，或重新执行评测；仅克隆仓库不能直接重算所有原始统计。

#### 11.2 来源文档题复测

```powershell
pwsh -File scripts/record_local_v3_business.ps1 -OutputDirectory data/evaluations/business-run
.venv/Scripts/python.exe scripts/summarize_v3_business_comparison.py --before data/evaluations/business-baseline/report.json --after data/evaluations/business-run/report.json --output data/evaluations/business-run/comparison.json
```

默认调用实际部署代码；`-UseWorkspaceBackend` 为工作区预演，不等于容器已同步。比较脚本核对完整运行、资料/题库、权重和预算的一致性，拒绝无效或不兼容比较。新的实测结果另存，不覆盖本报告固定记录。

#### 11.3 八格式与预算观测

```powershell
pwsh -File scripts/record_local_v3_evaluation.ps1 -Dataset Boundary
# 在 PowerShell 会话中逐预算测量演示题：
./scripts/record_local_v3_evaluation.ps1 -Dataset Demo -Budgets @(600,1200,1800)
```

模型成对比较使用 `scripts/compare_local_v3_models.ps1`，按 [模型比较说明](testing.md#retrieval-regression) 准备固定权重、资源限制和恢复流程。比较会操作评测模型服务，应与正常使用时间分开。

#### 11.4 相关代码回归与前端门禁

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_rag_v3.py tests/test_rag_v3_binary.py tests/test_rag_v3_context.py tests/test_rag_v3_strategy.py tests/test_rag_v3_adapter.py tests/test_rag_v3_evaluation.py tests/test_v3_demo_evaluation.py
.venv/Scripts/python.exe -m ruff check backend tests scripts/summarize_v3_report_metrics.py
Push-Location frontend
npm ci
npm run typecheck
npm run build
Pop-Location
```

测试使用共享 SQLite 测试库，应顺序执行。浏览器与真实 Docker 业务验收另行进行；命令列出不表示本次编写报告重新执行了全部检查。

### 12. 审计索引与局限

#### 12.1 主要依据

- [来源题逐题标注](../evaluations/retrieval/business/questions.md)、[来源登记与 SHA256](../evaluations/retrieval/business/sources.json)、[来源题前后比较](../evaluations/retrieval/business/comparison.json)、[此前首轮检索记录](evaluation_report.md#business-results-20260928)。
- [Pipeline 5 八格式观测](../evaluations/retrieval/local_context5_observations.json)、[pipeline 4 观测](../evaluations/retrieval/local_context_observations.json)、[历史边界观测](../evaluations/retrieval/local_boundary_observations.json)、[历史演示观测](../evaluations/retrieval/local_demo_observations.json)、[历史模型比较](../evaluations/retrieval/local_model_comparison.json)。
- [本机历史验收](evaluation_report.md#local-acceptance-20260928)、[正式状态](../evaluations/retrieval/status.json)、[独立复核清单](testing.md#review-checklist)、[正式流程](testing.md#retrieval-regression)。
- [本报告补充统计](../evaluations/retrieval/detailed_report_metrics.json)，由 `scripts/summarize_v3_report_metrics.py` 从原始文件重算。

#### 12.2 四份原始报告校验值

| 记录 | 本机路径 | SHA256 |
| --- | --- | --- |
| 来源题版本 3 | `data/evaluations/business-baseline/report.json` | `486cf0eab189ec99302d9f03a011233f59ba42fe1c7138c01a20cccd038f3254` |
| 来源题部署版本 4 | `data/evaluations/business-deployed/report.json` | `66c2ad3da18fa2e9c8ec5996978ec2f24d4e5141a81b8690953298a76f3901f3` |
| 来源题 pipeline 5 | `data/evaluations/business-pipeline5/report.json` | `626ebdb3a4daa34716720efb727ca7dabe779fed3edb5a1c979ce351205c893a` |
| 边界题版本 3 | `data/evaluations/9c5693299e8d4b9d970e35b2a78b7d66/boundary-1200.json` | `4b53aa84ab5af2cf2efd3dc1030818e22cb0f70a585514666bf96bad42dfb959` |
| 边界题版本 4 | `data/evaluations/boundary-context4/boundary-1200.json` | `84e8d55fa9c30168235db615f94cd44e3e086c1ec1261d56d25299affc9a9348` |
| 边界题 pipeline 5 | `data/evaluations/context5/boundary-1200.json` | `f6b688556deb9d14a7bfbe5b24fa7994143d2ee355cc3d7ee76f0ea9935e03af` |

来源题库 SHA256：`80149b83c42a421400892af1edc77e7a752ca759f7f1f9655c9c6bb5860395a1`。
八格式题库 SHA256：`2be6254c52bcb2643567a96483ee6c587845c959950d089e350ee5016fd05d22`。

#### 12.3 解释边界

1. 标签尚未独立复核，确定性原文匹配不完全覆盖语义等价、复杂否定和隐含条件。
2. 来源资料为项目维护文档与公开 PDF，合成题为短边界样本；结论不能直接推广到所有客户文档。
3. 尚未对生成回答、标准排名、意图路由、真实模型任务完成率和完整 token 费用判分。
4. 历史全套测试、最新针对性回归、隔离浏览器测试和实际 Docker 检索是不同环境/时点，数量不混加。
5. Docker 本机技术验收已通过；完整真实用户业务验收仍应由验收方按代表性场景签署。
6. 本报告仿照用户提供报告的组织结构，未复用其中的模型、医学数据、分数或验收结论。

**最终判定：本机 Docker、数据库、索引、真实检索模型和自动化 RAG 技术验收已经通过；独立人工复核、客户资料锁定验证、pipeline 5 模型比较和生成回答质量仍未完成，因此可以进入业务验收，但尚不能签署正式质量终验。**
