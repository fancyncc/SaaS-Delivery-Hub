# CPU 预生产实施记录

本轮仅操作 `saas-preproduction` Docker 项目，保留现有 SQLite 环境。目标依次为 M1 部署、M2 统一来源索引、M3 问答与状态、M4 业务及恢复验收。未经验证的阶段不标记完成。

## M1 部署入口

在项目根目录执行：

```powershell
.venv/Scripts/python.exe scripts/preproduction.py prepare
.venv/Scripts/python.exe scripts/preproduction.py preflight
.venv/Scripts/python.exe scripts/preproduction.py start
.venv/Scripts/python.exe scripts/preproduction.py status
.venv/Scripts/python.exe scripts/preproduction.py stop
```

`prepare` 保留已有预生产凭据，仅复用主环境中允许的回答生成配置。API/Worker 的 runtime 文件不包含数据库所有者密码与迁移地址，模型服务仅接收两项独立模型访问凭据。`stop` 保留数据卷；不提供默认删除卷入口。

首次 `start` 显式构建镜像并联网下载模型，后续模型服务仅从缓存离线加载。两个 BGE 模型固定为 `config/retrieval.cpu.env.example` 中已核对的官方提交版本。默认 4 个推理线程、一个模型进程，排队总数含运行任务最多 32；交互查询优先于未开始的建库请求，执行中的推理不中断。

预生产页面为 `http://localhost:18080`。模型服务和 OpenSearch 仅在容器网络内访问。平台超级管理员可使用 `GET /api/platform/retrieval/status` 查看依赖探测结果，不返回地址、密钥或客户资料。

## 环境与当前证据

- 宿主机：Intel i5-10210U，4 核 8 线程，约 16 GB RAM。
- Docker：Linux 引擎已启动，8 个逻辑 CPU，约 8 GB 可用内存。
- 开发与预生产 Compose 静态校验已通过。
- 请求优先级、取消后串行性、失败释放、预热就绪与凭据隔离测试已通过。
- 真实 CPU embedding 输出 512 维，L2 范数约 1；中文两候选 reranker 将相关片段排在首位，该次请求 549.05 ms（非 P95 指标）。两个模型预热、离线重启后就绪均通过。
- 独立 PostgreSQL 空库升级至 `0020_bge_cpu`，列类型为 `vector(512)`；应用角色 `saas_app` 的 superuser 与 bypassrls 均为 false。
- M1 基线后端回归：164 passed、4 skipped；全仓库 Ruff、前端类型检查与生产构建通过。跳过项不记为浏览器验收通过。
- 人工语料复核、真实检索质量、CPU P95 和完整业务验收尚未完成。

## 验收记录要求

M1 记录真实 512 维编码、重排排列、离线重启、模型版本及服务健康。M2/M3 记录四类来源的回填、版本切换、删除和权限回归。M4 记录三角色 80 人流程、故障注入、独立恢复库与对象存储核对，以及 1,000 片段的性能报告。自动编写的样例保留 pending_human 标记，不代替业务签署。

## M2 续作记录（2026-09-13）

- 新增 `0021_retrieval_sources` 已在独立预生产库升级，未操作原 SQLite 业务库。
- HTTP `/v1/chunks` 使用缓存中的真实 tokenizer、内部鉴权和后台优先级队列；真实 CPU 请求返回 15 个片段，固定 revision 校验通过。
- DOCX 保留表格行列，PDF 分块保留页码且不跨页拼接。
- 新增租户显式回填模块 `python -m backend.retrieval_backfill --tenant-id <id>`，仅读取业务数据库；更新、删除及重放测试通过。
- 真实 PostgreSQL 测试发现索引服务还需要现有 company_admin 数据库上下文，已修复 Worker 与回填；查询端继续校验原业务权限。
- `scripts/check_unified_preproduction.py` 仅允许 `saas_preproduction` 库并生成明确标记的原创测试数据。实测四来源 ready、真实混合检索返回 4 条；公司公共范围仅 1 条，非所有者私有访问被拒绝。测试租户：`9340fca3-8a63-416e-a084-f03100a4a8ae`。
- 本次检索基线 32 项、新增分块格式 6 项、来源生命周期/私有权限/三次失败上限 3 项通过；Ruff 与 mypy（61 个后端文件）通过。
- 进度使用短期 Redis 记录，按来源、代次和尝试次数隔离；数据库仍负责最终发布。该进度展示尚待接口与页面接入验证。
- M2 尚未最终验收：需补齐后台解析边界、完整回填与恢复记录；M3 页面/问答统一接入及 M4 业务、性能、质量验收仍待推进。
