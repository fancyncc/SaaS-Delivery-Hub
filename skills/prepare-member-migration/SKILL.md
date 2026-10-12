---
name: prepare-member-migration
description: Prepare member CSV migration instructions from supplied field mappings, preview results and target configuration; explain row errors and readiness without importing or approving data.
metadata:
  version: "1"
---

# 成员迁移准备

使用 preview_import 的实际结果整理字段映射、目标部门与固定角色要求。说明无效字段、冲突成员、受影响行和具体修正方法，不编造缺失值。

区分新成员、已存在的一致成员和需要整改的冲突。目标未初始化时说明准备阶段限制。校验失败是有效检查结果，不建议重复提交相同文件。

输出调用方要求的报告结构；保留源文件和错误行的对应关系，避免在公开查询中加入员工姓名、邮箱或 CSV。预览不代表可执行或已批准；整改后的文件重新校验和审批，执行后以持久化结果和回读凭证确认成功。
