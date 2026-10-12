# 首轮 RAG 质量题库

共 5 份真实来源、29 道题（24 道有答案、5 道无答案）。前五题是优先检查的问题。

资料包括仓库维护文档的固定快照和 NIST 公开 PDF，不是客户业务资料。答案标签由代理起草，尚未独立复核；本题库用于开发诊断，不替代锁定验证集。

测试候选召回、最终证据、引用位置、预算和无答案误报；证据覆盖不等于生成回答正确率。

## business-01：知识库 PDF 文件大小、页数和 OCR 页数上限分别是多少？

来源：[原文](sources/RAG_BINARY_DOCUMENTS.md)

核对片段：`10 MiB、100 页`；`最多 30 页`

## business-02：记忆候选是否只打开部署开关就会自动提取？

来源：[原文](sources/CONTEXT_MEMORY.md)

核对片段：`用户还必须通过`；`显式启用`；`auto_extract`

## business-03：聊天流式回答中途失败后会保存半截答案吗？

来源：[原文](sources/CHAT_AGENT.md)

核对片段：`不保存半截答案`；`重试相同 request_id 返回已保存的`

## business-04：PostgreSQL 备份是否包括 S3 交付物？应该另外备份什么？

来源：[原文](sources/PREPRODUCTION_RUNBOOK.md)

核对片段：`PostgreSQL dump 不包含 S3 对象`；`需另外备份 artifacts 卷`

## business-05：According to NIST IR 8397, what minimum code coverage should structural test cases reach?

来源：[原文](sources/NIST.IR.8397.pdf)，PDF 第 12 页（文件页序）

核对片段：`at least 80 % coverage`

## business-06：XLSX 知识文档会计算公式或打开外部链接吗？

来源：[原文](sources/RAG_BINARY_DOCUMENTS.md)

核对片段：`已保存的单元格值`；`不计算公式，不打开外部链接`

## business-07：PPTX 解析能识别图片内容吗？

来源：[原文](sources/RAG_BINARY_DOCUMENTS.md)

核对片段：`文字框和表格；图片内容不识别`

## business-08：知识库上传的解析结果最多允许多少字符和节点？

来源：[原文](sources/RAG_BINARY_DOCUMENTS.md)

核对片段：`最多 15 万字符或 1 万结构节点`

## business-09：知识库 PDF 和 Office 隔离解析的最长时间分别是多少？

来源：[原文](sources/RAG_BINARY_DOCUMENTS.md)

核对片段：`PDF 最长 10 分钟`；`Office 最长 2 分钟`

## business-10：项目知识文档失败后需要什么权限才能重试？

来源：[原文](sources/RAG_BINARY_DOCUMENTS.md)

核对片段：`project.document.submit`

## business-11：聊天 PDF 附件的支持范围和大小上限是什么？

来源：[原文](sources/RAG_BINARY_DOCUMENTS.md)

核对片段：`仅支持带文字层的 PDF 附件`；`2 MiB 上限未改变`

## business-12：记忆候选有效期多久，已拒绝的候选会重复提出吗？

来源：[原文](sources/CONTEXT_MEMORY.md)

核对片段：`候选有效期 30 天`；`已拒绝的同一候选不重复提出`

## business-13：相同 key 的私有记忆在哪个作用域优先？

来源：[原文](sources/CONTEXT_MEMORY.md)

核对片段：`conversation > project > workspace > user`

## business-14：维护摘要的模型会读取助手回答、文档或工具结果吗？

来源：[原文](sources/CONTEXT_MEMORY.md)

核对片段：`不读取助手回答、文档或工具结果`

## business-15：保存记忆候选并替换冲突条目需要哪些版本参数？

来源：[原文](sources/CONTEXT_MEMORY.md)

核对片段：`expected_version`；`replace_id`；`replace_version`

## business-16：聊天工具检索最多进行多少轮，每轮允许几个工具？

来源：[原文](sources/CHAT_AGENT.md)

核对片段：`最多两轮、每轮一个只读检索工具`

## business-17：聊天 SSE 包括哪些事件？

来源：[原文](sources/CHAT_AGENT.md)

核对片段：`step`；`delta`；`done`；`error`

## business-18：MCP 接口是否支持匿名访问或第三方 OAuth？

来源：[原文](sources/CHAT_AGENT.md)

核对片段：`不是公开匿名 MCP 服务`；`尚未提供第三方 OAuth 接入`

## business-19：预生产导入 CSV 的人数、管理员和部门要求是什么？

来源：[原文](sources/PREPRODUCTION_RUNBOOK.md)

核对片段：`80 人 CSV`；`包含一名 admin`；`部门须与目标配置一致`

## business-20：执行人排队后被撤销权限，worker 能继续实施吗？

来源：[原文](sources/PREPRODUCTION_RUNBOOK.md)

核对片段：`禁止 Worker 提升权限继续执行`

## business-21：What should automated verification ensure about static analysis and test results?

来源：[原文](sources/NIST.IR.8397.pdf)，PDF 第 13 页（文件页序）

核对片段：`ensure that static analysis does not report new weaknesses`；`check results accurately`

## business-22：NIST IR 8397 推荐检查代码中的哪些硬编码秘密？

来源：[原文](sources/NIST.IR.8397.pdf)，PDF 第 14 页（文件页序）

核对片段：`hardcoded passwords`；`private encryption keys`

## business-23：According to NIST IR 8397, when should threat modeling be repeated during development?

来源：[原文](sources/NIST.IR.8397.pdf)，PDF 第 12 页（文件页序）

核对片段：`multiple times during development`；`especially when developing new capabilities`

## business-24：NIST IR 8397 说黑盒测试应基于什么？

来源：[原文](sources/NIST.IR.8397.pdf)，PDF 第 14 页（文件页序）

核对片段：`functional speciﬁcations or requirements`

## business-25：知识库是否支持 DWG 文件，并可保证 CAD 图纸识别准确率为 99.99%？

来源：[原文](sources/RAG_BINARY_DOCUMENTS.md)

核对片段：来源中没有支持该结论的证据，应返回证据不足。

## business-26：该系统对所有用户提供无限期免费云端记忆存储的合同条款是什么？

来源：[原文](sources/CONTEXT_MEMORY.md)

核对片段：来源中没有支持该结论的证据，应返回证据不足。

## business-27：这个产品已通过哪家机构的 ISO 27001 认证，证书编号是什么？

来源：[原文](sources/CHAT_AGENT.md)

核对片段：来源中没有支持该结论的证据，应返回证据不足。

## business-28：正式生产环境保证的每月 SLA 赔偿比例是多少？

来源：[原文](sources/PREPRODUCTION_RUNBOOK.md)

核对片段：来源中没有支持该结论的证据，应返回证据不足。

## business-29：What is NIST IR 8397's mandatory subscription price for the SaaS_Agent product?

来源：[原文](sources/NIST.IR.8397.pdf)

核对片段：来源中没有支持该结论的证据，应返回证据不足。

## 重现

运行 `scripts/record_v3_business_runs.py --cases evaluations/v3/business/cases.jsonl --output <raw-report.json>`。

在本机 Docker 中使用 `scripts/record_local_v3_business.ps1`；资料在未提交的临时租户中索引，完成或异常后回滚 SQL 并清理相应搜索条目。

人工复核应逐题检查原文、必要条件、无答案标签和错误引用；修改标签后生成新版本并重新记录基线。
