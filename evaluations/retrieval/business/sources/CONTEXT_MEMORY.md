# 上下文与记忆：分阶段交付

本版本按 P0/P1/P2 独立开关提供统一 token 预算、结构化状态和滚动摘要，以及私有历史混合召回、分作用域记忆、需确认的记忆候选、受验证的 Agent 经验和评分影子模式。默认关闭新策略，先验收 P0，再逐阶段启用。所有新表与兼容数据转换由迁移 0026、0027 管理。


## 数据流和信任边界

1. 用户发消息时校验所有权、空间与项目权限。旧会话按需入维护队列，即使本次输入超预算，已有记录仍能进行后台压缩。
2. 按 token 预算选取本轮证据、有效会话状态、近期原文、摘要、用户记忆和可验证的助手回答。所有最终生成请求再次进行完整请求预算校验。
3. 回答及引用校验成功后，将稳定消息 ID 和维护任务在同一事务提交。失败回答不进入历史。
4. 后台每次处理最多 8 轮已提交记录，生成独立快照，不修改对话版本号、不写长期记忆、不改变业务状态。
5. 下一轮补入快照尚未覆盖的原文；必要内容无法容纳时返回 422，不静默跳过中间轮次。

维护模型只读取用户原文和先前派生状态，不读取助手回答、文档或工具结果。状态分为 `goals`、`entities`、`constraints`、`decisions`、`open_questions`、`next_steps`；每项保留用户原文、消息 ID、轮次和替代关系。状态条目的文本必须逐字匹配原文。含糊或未明确确认的决定进入待澄清项。明确的“某事项改为／改成”才允许替代同事项旧记录；其他更正保守保留为待澄清，不能靠模型推断删除旧约束。

摘要始终标记为用户表述的派生信息，不能证明项目事实。离线模式只摘取原文，并标记 `extractive`；不具备真实模型的指代分析和语义摘要能力。真实模式使用结构化输出，检查来源 ID 和摘要 token 上限。助手历史答案仅在非空引用全部与当前证据一致时才可作为低优先级历史加入，不能成为业务证据。

附件变更使快照整体失效；源消息摘要值、用户、空间和生成版本不匹配也会失效。知识库版本和实时状态不会写入快照，因此始终由本轮检索和授权查询提供。会话或项目访问被撤销后，快照读取与任务执行均重新拒绝访问。删除会话会清理其任务和快照。

## 配置与启用

先执行数据库迁移 `python -m alembic upgrade head`，使用最小权限 PostgreSQL 应用角色时按现有部署流程重新授予新增表 DML 权限。新增迁移为 `0026_chat_context`、`0027_scoped_memory`，兼容 SQLite 与 PostgreSQL；不修改生产数据库配置或自动对生产运行迁移。

| 配置 | 默认值 | 含义 |
|---|---|---|
| `CHAT_CONTEXT_MODE` | `off` | `off` 旧历史组装；`shadow` 维护并测量新上下文但仍使用旧组装；`on` 使用新上下文 |
| `CHAT_CONTEXT_LOCAL_WORKER` | `false` | 本地 API 进程启动独立维护循环，每 5 秒扫描 |
| `CHAT_CONTEXT_TOKENS` | `12000` | 聊天模型完整输入的应用上限 |
| `AGENT_CONTEXT_TOKENS` | `12000` | v2 Agent 输入上限，单位修正为 token |
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

## 持久化、重试与接口

- `chat_context_snapshots`：用户／空间／对话归属、已处理轮次、摘要覆盖轮次、原文摘要值、附件摘要值、结构化状态、摘要、生成次数、生成版本和模式。
- `chat_context_tasks`：按对话合并任务，保存目标版本、状态、租约、重试次数和下次执行时间。处理前后都验证执行人的权限；租约过期可恢复，旧执行者不能覆盖新快照。
- 连续失败最多 5 次，退避上限 300 秒；失败不影响已保存回答。用户下一次提问会重新调度未完成的历史任务。
- 最近 8 轮为原文窗口；窗口外新增至少 8 轮或预组装 token 占用超过 80% 时更新摘要。
- `GET /api/chat/conversations/{id}/context` 返回 `state`、`summary`、`processed_turn`、`summary_until`、`generation`、`producer_version`、`mode`、`status`、`error_code` 和 `target_turn`。沿用对话所有权与项目权限检查，不返回内部推理。
- 发送消息与 SSE 事件格式兼容，历史消息新增稳定 `id`，保留原有 `request_id`。

关闭 `CHAT_CONTEXT_MODE` 可回到原有历史选择策略，保留快照、任务、消息及长期记忆；完整请求预算保护始终生效。回滚功能不执行降级迁移，也不删除新数据。

## 观测与验收

Prometheus 新增 `saas_context_tokens`（估算、预留、实际输入／输出及误差）、`saas_context_layer_tokens`、`saas_context_trimmed_total`、`saas_context_lag_turns`、`saas_context_jobs_total`。标签只有固定类别，不包含用户、项目、消息 ID 或原文。Agent 状态分别保留 `tokens_estimated`、`tokens_actual`、`tokens_unsettled` 和用于限额的 `tokens_reserved`，跨阶段及恢复保留。

回归命令：

```text
python -m pytest tests/test_context_budget.py tests/test_conversation_context.py tests/test_context_migration.py tests/test_chat.py tests/test_chat_streaming.py tests/test_chat_scope.py tests/test_chat_management.py tests/test_agent_loop.py tests/test_state_machine.py -q
```

`evaluations/context_cases.jsonl` 含 30 个固定长对话场景，覆盖 50／150 轮、六类约束、明确更正及主题切换。测试验证状态保持和来源检查；它们是确定性回归，不代表真实模型语义质量已达到 95%。真实模型上线前，仍需对实际模型配置跑人工标注的端到端问答评估，并验证 PostgreSQL 实例上的 RLS、并发和实际 tokenizer／usage 误差；SQL 生成测试不能替代 PostgreSQL 运行验收。


## P1：历史和分作用域记忆

设置 `CHAT_HISTORY_ENABLED=true` 后，已校验的消息切换为 `chat_messages` 中独立的 user/assistant 记录，保留请求 ID、时间、步骤、引用和回答模式。迁移 0027 先逐条回读比较再建立校验标记，保留原 JSON；切换后的会话停止写旧 JSON。关闭新历史召回后，已经切换的会话仍从独立记录读取和写入，以免丢失新消息。

对话详情及发送结果默认返回最近 50 轮和 `next_before`；`GET /api/chat/conversations/{id}/messages?before=...&limit=50` 向前翻页。默认最大 2,000 轮，未切换的 P0 对话保留 200 轮。前端提供加载旧消息入口；复制对话会读取所有页面。

历史召回仅在当前用户、当前空间、当前对话内进行，索引用户原文，以 BM25 + 生成配置独立的现有 embedding 服务进行 RRF 融合，最多返回 6 个片段及相邻用户原文。使用单独的 `conversation_history` 类别，不生成项目资料引用。向量服务不可用时返回词项召回并记录降级。后台按维护进度建立向量；无索引时词项检索仍可用。新历史、近期原文、摘要和状态根据消息 ID 去重。索引 generation 使用模型／revision 配置指纹，旧模型向量不混用。

设置 `CHAT_MEMORY_ITEMS_ENABLED=true` 后，使用私有 Memory Item。`user` 只允许显式通用偏好，跨空间生效；`workspace` 默认限制当前空间；`project` 和 `conversation` 进一步限制适用范围，均不共享给其他成员。相同 key 按 conversation > project > workspace > user 覆盖，过期或撤销条目不进入上下文。同范围重复 key 返回 409，不能自动覆盖。

`GET/POST /api/chat/memory-items`、`PATCH/DELETE /api/chat/memory-items/{id}` 提供管理接口；更新、删除需版本号。条目按相关度和预算逐项进入上下文，不再要求整段全部带入。旧记忆原样迁移为 workspace 的 `__legacy_workspace__` 条目，由 `/api/chat/memory` 继续维护；即使关闭新功能也保持兼容文本同步，不自动提升跨空间范围。前端长期记忆窗口提供条目管理和范围选择。

## P2：候选、经验与评分

`CHAT_MEMORY_CANDIDATES_ENABLED=true` 只是部署开关；用户还必须通过 `PUT /api/chat/memory-preferences` 显式启用 `auto_extract`。后台仅为“请记住… / 以后请… / 我的偏好是… / 我是…”这类明确的用户原文生成 workspace 候选，不对模型推断、助手回答或文档自动提取。候选有效期 30 天，按归一化原文去重；已拒绝的同一候选不重复提出。

`GET /api/chat/memory-candidates` 返回原文、来源消息及重复／冲突条目。`POST /api/chat/memory-candidates/{id}/accept|reject` 需要 `expected_version`；替换冲突条目还需明确的 `replace_id` 和 `replace_version`。来源失效、权限撤销、过期或并发变更时拒绝保存。候选状态和正式条目在同一事务提交；前端显示待替换内容，并使用“确认替换并保存”按钮，不静默覆盖。

`AGENT_EXPERIENCE_ENABLED=true` 扩展三类代码维护的经验模板：完整阶段的按序工具凭证验证 workflow；成功高风险工具加对应 approved 审批验证 constraint_pattern；成功文件校验加无错误且有行数据的 ImportJob 验证 validation_rule。每类都通过独立注册验证器读取持久化证据。未通过、缺失验证器或工作流版本不同的经验不召回。模板建议固定且不能变成可执行规则，历史审批永不替代当前审批。

`CONTEXT_SCORING_MODE=shadow` 记录与固定优先级选择的差异，不改变回答；验收后可设为 `on`。分数为相关度 40%、可信类别 30%、时效性 15%、任务价值 15%，同分优先较短块。必需块不参与淘汰，当前证据始终排在历史和建议之前。`saas_context_scoring_difference` 记录影子差异，`saas_history_recall_total` 记录混合召回和降级情况。

新增阶段回归：`python -m pytest tests/test_scoped_memory.py tests/test_context_budget.py -q`。包含 2,000 轮分页、消息／旧记忆迁移、关开关后数据保留、作用域覆盖、过期、来源删除、候选冲突与拒绝、历史语义融合／降级和验证经验场景。真实模型质量、线上 PostgreSQL 并发和服务商 token 误差仍需部署环境验收。
