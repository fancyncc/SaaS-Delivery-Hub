# 全流程实施与验收记录

更新：2026-09-06。目标：内置模拟 SaaS 真实持久化、独立审批人验收、完整功能与预生产验证；不发布生产。

## 当前事实

已实现配置写入/回读、材料绑定审批、实际成员导入、材料准备状态、版本交付物、真实上线检查、整改材料修改、配置方案重新审批、角色化待办、知识管理、SMTP 队列、S3 适配、Celery/Outbox/LangGraph 执行和预生产部署配置。

本地离线模式的 80 人三角色浏览器主流程已通过：公司管理员启动，独立审批人审批，实施顾问提交 CSV，最终数据库中有 80 名业务成员和 6 份交付物。此结果不等于 PostgreSQL、真实模型或完整预生产验收通过。

## 需求追踪

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

## M0–M5 门槛

- M0：文档基线和类型修复已落实；PostgreSQL/Compose 启动门槛仍缺证据。
- M1：内置 SaaS 业务闭环在 SQLite 通过。部门、模板、状态、自定义字段名称通过配置文档持久化；业务角色采用固定 admin/manager/member/viewer，尚非完整可自定义 RBAC 产品。
- M2：主要页面和多角色离线闭环已验证；独立整改任务实体、失败行专用重试及 Markdown/JSON 下载已补齐。Trace 与离线评测产品页已实现，真实模型实验及跨 Worker Span 聚合仍需补齐。
- M3：执行、检查点、Outbox 与邮件已接入。本地模拟故障已验证；真实 Celery 杀进程及跨 PG 检查点恢复未验收。
- M4：适配器和知识接口已接入；65 篇授权资料、模型地址/名称/凭据、真实 embedding 及质量基线缺失，不标记完成。
- M5：预生产 Compose、Secret、监控和恢复脚本已交付。本机主 Compose 已完成备份、迁移及业务冒烟验收；隔离预生产环境的完整故障演练和业务签署尚未完成。

## 验证入口

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

## 仍需完成

1. 在可用 Docker Linux 环境执行预生产构建、PG 全链迁移、RLS/最小权限检查及备份还原；记录实际 RPO/RTO 和容量结果。
2. 提供模型配置及 65 篇授权知识，执行结构化成功/失败、向量召回、引用准确性与人工复核评测。
3. 补齐可自定义业务角色和字段类型、真实模型实验记录与分布式 Trace 聚合；已有页面只展示持久化节点记录及离线检索评测，不把记录创建时间当成节点耗时。整改任务只由实际检查结果关闭，不因提交反馈自动关闭。
4. 补齐完整跨 Worker Trace、告警通知路由、备份年龄指标和 PITR；现有定时备份为整库快照，不是 PITR。
5. 完成真实 broker/对象存储/SMTP 故障注入及并发测试，再关闭 M3/M5。

## 接口参考

本轮新增入口：Run 详情中的“查看执行 Trace”（`/app/runs/:id/trace`）；平台管理中的“评测历史与对比”（`/platform/evaluations`）。平台超级管理员可运行离线评测，平台审计员只能查看与下载，客户账号无权访问；平台历史只查询全局评测，不混入租户专有结果。POST `/api/platform/evaluations/run` 现在要求 `Idempotency-Key`，相同账号与请求键返回原记录。对比要求数据集、语料校验和及评测器版本一致。

结构化模型接口按 [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) 实现；异步框架参考 [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 和 [Celery tasks](https://docs.celeryq.dev/en/v5.5.0/userguide/tasks.html)。外部服务调用仅在显式配置后启用。
