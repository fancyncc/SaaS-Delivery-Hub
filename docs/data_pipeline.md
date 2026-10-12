# 数据处理与索引管线

企业文档、聊天附件、成员 CSV 和公开评测资料分别走受控入口；授权、原件、版本、校验记录及来源位置需要随处理结果保留。磁盘上的知识资料目录不承担业务知识库的持久化职责。

## 文档导航

[agent.md](agent.md) · [api.md](api.md) · [architecture.md](architecture.md) · [data_pipeline.md](data_pipeline.md) · [deployment.md](deployment.md) · [evaluation_report.md](evaluation_report.md) · [rag.md](rag.md) · [testing.md](testing.md)

## 本文内容

- [PDF、Office 与结构化文档处理](#document-formats)

## 数据入口与处理顺序

| 数据 | 入口与处理 | 关键边界 |
| --- | --- | --- |
| 企业/项目文档 | `/api/knowledge`、`/api/knowledge/files` → 保存原件 → 注册索引来源 → Worker 解析/切分 → Embedding → SQL 向量/OpenSearch | 项目权限、租户、版本、原件摘要和结构位置；最新版本未就绪不回退旧版 |
| 聊天附件 | `/api/chat` 下附件接口 → 解析 → 绑定对话 → 当前会话检索 | 会话所有权、范围和上传限制；不自动变成公司通用知识 |
| 实施成员 CSV | `/api/imports/validate` → 字段及行级校验 → 独立审批 → `/api/imports/{id}/execute` | 审批绑定材料，重新校验执行人权限，保留执行回执 |
| 公司成员邀请 | `/api/company/member-imports` → 校验 → 邀请/开通 | 与实施目标系统的成员导入不同，遵守公司管理权限 |
| CMRC | 固定公开 JSON → 抽样 cases/原文 → 临时租户真实索引 → 检索/生成 → 离线评分 | 标准答案只参与评分；SHA-256 冻结；失败计零；清理临时 SQL 和搜索条目 |

## 存储与目录

- 原件与结构化索引由 `knowledge_index` 模块维护；磁盘目录删除不等于删除业务知识文档。
- 默认主机模型缓存为 `data/models`，`RETRIEVAL_MODEL_CACHE` 可覆盖；Docker 检索镜像继续使用 `/models` 命名卷。
- `evaluations/` 保存版本化题库、原文快照、统计 JSON 和许可；其中 Markdown 原文及 `questions.md` 属于评测输入，应与文档整理区分。
- `data/evaluations/` 保存下载的公开资料、运行日志、逐题观测及评分；`backups/` 保存本机备份，均不入版本控制。
- 同一评测版本的源文件、题目、偏移与哈希不得随说明文档修改；调整语料或标签须生成新版本。


<a id="document-formats"></a>

## PDF、Office 与结构化文档处理

企业知识库、项目文档及任务文档的文件上传共用 知识检索 异步解析和索引。新增格式为 PDF、PPTX、XLSX；现有 DOCX、Markdown、TXT、CSV、JSON 保持可用。旧版 DOC、PPT、XLS 与加密 PDF 不支持，需先转换或解密。

| 格式 | 文件上限 | 解析范围 | 位置标识 |
| --- | ---: | --- | --- |
| PDF | 10 MiB、100 页 | 文字层；无文字或仅有少量文字的图片页使用中英文 OCR，最多 30 页 | 页码、页内块 |
| PPTX | 5 MiB、200 张 | 文字框和表格；图片内容不识别 | 幻灯片、形状、段落或单元格 |
| XLSX | 5 MiB、50 表 | 已保存的单元格值；不计算公式，不打开外部链接 | 工作表、行、列 |
| 其他已有格式 | 2 MiB | 原有解析行为 | 原有位置字段 |

提取结果最多 15 万字符或 1 万结构节点。PDF OCR 结果及未识别图片在详情页显示告警。无内容流的空白页不占 OCR 页数；有图片但无法提取文字的页面会使整份文档失败，不会静默遗漏。OCR 质量受扫描清晰度影响，重要数字须核对原件。

上传后状态依次为 `pending`、`parsing`、`indexing`、`ready`；损坏、加密、超限和无法识别的文件显示失败原因。解析在隔离子进程中执行，PDF 最长 10 分钟，Office 最长 2 分钟。原件保存于 `retrieval_documents.raw`，增大文件上限会增加数据库及备份体积。历史记录若只有提取文本、没有原始 PDF／PPTX／XLSX，显示“需重新上传原文件”；不能从旧正文伪造原件索引。

失败的项目文档可由具备 `project.document.submit` 权限的项目成员在项目文档页或详情页重试；公司通用文档由公司管理员重试。知识检索 查询只读取索引状态与引用所需的文档字段，原件仅在解析时读取。校准发布门槛已包含 PDF/PPTX/XLSX 的逐格式指标；当前合成样本尚待独立复核与真实检索评测。

知识检索 最终证据保留页码、幻灯片或工作表位置，聊天和 Agent 仅引用这些最终证据。聊天附件仍走独立授权路径；目前仅支持带文字层的 PDF 附件，其 2 MiB 上限未改变。

本机发布使用 `scripts/deploy_local.ps1` 完成备份、迁移及 Docker 构建。`RAG_PDF_ENABLED=false` 可同时停止 PDF 新上传与知识检索，`RAG_OFFICE_ENABLED=false` 对 PPTX/XLSX 同理；已上传原件与索引保留，恢复开关后可继续使用。观察 `/metrics` 的索引状态、各格式数量、解析时长 p95、当前已索引文档的 OCR 页数及最旧待处理时间，并在文档详情查看逐份告警。
