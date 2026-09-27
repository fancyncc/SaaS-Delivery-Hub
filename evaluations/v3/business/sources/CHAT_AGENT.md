# 聊天 Agent 与本地真实 RAG

## 链路

网页通过 POST `/api/chat/conversations/{id}/messages`，携带 `stream: true` 接收 SSE：

1. 判断问题：明确的问题保留原文；指代模糊的问题由结构化模型决定保留、重写或澄清。重写仅用于检索，原始问题和项目编号保留。
2. 工具执行：Chat Completions 模式使用模型原生 `tools` / `tool_calls` / `tool` 消息协议，最多两轮、每轮一个只读检索工具。第二轮仅补充缺失信息。
3. 观察：工具执行器固定当前用户、租户、对话和项目范围；每次重新授权。统一客户来源走 BM25 与 BGE 向量召回、RRF 融合、BGE 重排、去重；平台手册及实时状态单独作为对应类型证据。
4. 回答：真实模型 token 增量直接发送到前端。完成标记、长度及引用编号校验成功后进行版本条件写入；失败输出 `error`，不保存半截答案。重试相同 request_id 返回已保存的 `done`。

SSE 事件包括 `step`、`delta`、`done`、`error`。`step` 是实际执行状态与决策摘要，不是模型内部思维链。简单问题不需要强制多轮循环；澄清问题不进行无意义工具调用。当前工具均只读，业务实施仍由原有审批工作流执行。

Responses 模式支持真实正文流式输出，但当前原生工具循环仅适配 Chat Completions；Responses 使用同一授权检索工具的确定性调度。保留 `stream: false` 的原 JSON 接口兼容旧客户端。

## MCP

`/api/mcp` 提供固定版本 `2025-11-25`、无状态 Streamable HTTP JSON 响应，支持 initialize、ping、tools/list、tools/call。GET/DELETE 返回 405。工具为 `search_authorized_context`，参数只有 conversation_id、query。内嵌 Agent 使用与 MCP 相同的工具分发器，避免经网络回传用户会话凭据。

当前认证沿用本产品客户会话 Cookie 与 `X-CSRF-Token`；客户端需要发送 `Accept: application/json, text/event-stream`，初始化后发送 `MCP-Protocol-Version: 2025-11-25`。这不是公开匿名 MCP 服务，也尚未提供第三方 OAuth 接入或外部 MCP 服务目录。平台账号不能读取客户聊天。权限在工具执行时检查，不依赖只读提示字段。

协议参考：https://modelcontextprotocol.io/specification/2025-11-25/basic/transports

## 本地部署

`docker-compose.local-rag.yml` 给 OpenSearch 和 BGE 服务添加仅回环地址的端口。先下载权重，再启动模型服务；模型服务要求随机访问密钥，API 的 embedding/reranker 密钥须与之保持一致。

```powershell
docker compose --env-file .env -f docker-compose.yml -f docker-compose.local-rag.yml --profile model-setup run --rm model-download
docker compose --env-file .env -f docker-compose.yml -f docker-compose.local-rag.yml --profile rag up -d postgres redis opensearch retrieval
.venv\Scripts\python.exe -m backend.local
```

本地配置需要 RAG_MODE=real、PostgreSQL/pgvector、OpenSearch，以及本地模型服务地址。EMBEDDING_MODE=online 表示 API 经 HTTP 访问本机 BGE 容器，模型计算仍在本机 CPU 完成。`LOCAL_INDEXER_ENABLED=true` 启用单进程索引调度；生产多进程部署使用原有 Celery indexer/beat，关闭本地调度。

OpenSearch 和检索模型 HTTP 客户端直接连接所配置的依赖地址，不继承系统代理。生成模型保持独立配置：本次青云示例为 `MODEL_BASE_URL=https://top.qingyuntop.ai/v1`、`MODEL_NAME=qwen-plus`、`MODEL_API_STYLE=chat_completions`，对应 POST `/v1/chat/completions` 和 Bearer 请求头。不要把 `/pricing` 网页地址填入模型 API 配置。

SQLite 迁移用 `scripts/migrate_local_rag.py`：先创建独立 PostgreSQL 数据库并运行 Alembic，再设置 SAAS_ENV_FILE 指向该库的私有 owner 配置。脚本创建 SQLite 备份，拒绝目标已有业务数据，事务内复制并核对所有来源主键；检索索引不复制，必须回填并重建。运行 API 使用非超级用户的数据库角色。

知识文档 V3 已支持扫描 PDF 页的 OCR、PPTX 文字和 XLSX 单元格；聊天附件仍只解析带文字层的 PDF。后续范围包括复杂版面与表格恢复、多查询检索质量评估、证据蕴含校验、召回阈值标定、长文整体总结与引用句级准确率。当前编号校验不等于语义事实已被自动证明。

## 切换宿主机与 Docker 启动

两种启动方式必须指向同一数据库：`.env` 中 `POSTGRES_DB` 必须等于宿主机 `DATABASE_URL` 的数据库名。Compose 会自行组装容器数据库 URL，并不会直接沿用宿主机的 `DATABASE_URL`。数据库名不一致会导致同一账号在一侧存在、另一侧登录返回 401。

容器默认通过 `retrieval:8010` 和 `opensearch:9200` 访问依赖；宿主机则使用 `127.0.0.1` 的映射端口。需要容器访问外部检索服务时，单独设置 `DOCKER_EMBEDDING_BASE_URL`、`DOCKER_RERANKER_BASE_URL` 和 `DOCKER_OPENSEARCH_URL`，避免宿主机地址污染容器配置。

## 青云生成模型验收

依据[服务商地址文档](https://qingyuntopai.apifox.cn/doc-9285041)，使用 `https://top.qingyuntop.ai/v1`，通过 Bearer 认证调用 `/chat/completions`。`qwen-plus` 的公开模型目录标为国产模型分组；令牌所属站点、允许分组和模型权限须在服务商控制台核对。不能仅凭公开价格页判断某一令牌已获调用权限。

保存 `.env` 后，已有容器不会自动更新环境变量，须重新创建 API 容器。以下检查读取容器实际配置，依次验证普通回答、原生工具调用和应用使用的流式解析器。测试会产生少量真实调用费用，首个失败即停止，不打印密钥或模型正文：

```powershell
docker compose --profile rag up -d --no-deps --force-recreate api
Get-Content -Raw scripts/verify_model_api.py | docker compose exec -T api python -
```

只有输出 `ready: true` 才表示上述生成能力全部通过；配置完整、容器健康或检索成功不等于生成模型验收通过。`401 / Invalid token` 表示当前网关拒绝认证，先核对令牌所属站点和网关；保留诊断中的请求编号供服务商定位。不要通过更改模型名、增加重试或返回模拟答案掩盖认证失败，也不要把密钥发送到未经确认的备用域名。
