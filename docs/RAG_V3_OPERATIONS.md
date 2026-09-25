# V3 检索运行说明

RAG 查验、聊天及 Agent 的企业和项目知识文档共用 V3 最终证据。聊天附件、交付物、平台手册和实时状态仍按各自授权路径读取。知识文档索引未就绪时返回 `index_unavailable`，不会静默使用其他知识索引。

## 相关性判断

本机默认 `RAG_V3_RELEVANCE_MODE=rules`，使用重排分数与 `RAG_V3_MIN_RERANK_SCORE=0.5` 的规则阈值；接口返回 `quality_calibrated=false`。该分数不代表概率或独立质量验收。更换重排模型后须重新检查阈值。

`RAG_V3_RELEVANCE_MODE=calibrated` 要求固定模型版本、校准发布文件、锁定验证报告和独立复核；任一条件不满足即返回 503。发布门槛逐格式检查 MD、TXT、DOCX、CSV、JSON、PDF、PPTX 和 XLSX。当前 192 个合成边界问题仅用于结构与流程回归，独立复核、真实资料评测和两模型对比仍未完成。详情见 `evaluations/v3/README.md`。

## 索引恢复

Docker 使用 `RAG_V3_INDEXING_ENABLED=true`，由单实例 beat 调度、indexer 消费索引任务。API 内置索引循环保持关闭。历史文本资料可分批注册；只有提取文本而没有原件的 PDF、PPTX 和 XLSX 必须重新上传。失败资料在详情页显示原因；有权限的项目成员或公司管理员可重新加入队列。模型身份变化后会重建不兼容的向量。

## 验证与故障处理

检查 `/api/knowledge/inspect` 的 `pipeline=v3`、`chunks` 与 `evidence_text`，再用真实资料核对页码、幻灯片或工作表位置和聊天、Agent 引用。用 `/metrics` 观察 pending、failed、最旧待处理时间与解析耗时。索引故障先修复消费者和原件，再重试失败资料。需要暂停新索引时设置 `RAG_V3_INDEXING_ENABLED=false`；校准策略故障可将 `RAG_V3_RELEVANCE_MODE` 设回 `rules`，保留已写入的索引数据。
