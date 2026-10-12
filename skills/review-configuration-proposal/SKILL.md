---
name: review-configuration-proposal
description: Review a SaaS implementation configuration proposal against supplied customer requirements and current configuration, identifying differences, affected roles and unresolved risks before human approval.
metadata:
  version: "1"
---

# 配置方案评审

用提供的需求和当前配置逐项比对方案。列出原值、建议值、理由、受影响部门或角色、风险和需要客户确认的问题。把客户明确要求和实施建议分开。

本项目目标是内置模拟 SaaS，业务角色仅有 admin、manager、member、viewer。角色需求超过现有能力时列为差距，不声称已实现动态 RBAC 或第三方 CRM 配置。

每项产品能力须引用当前资料；没有依据时标记待核实。输出保持调用方提供的结构化 Schema。配置方案是待审批建议，不表示已应用；批准材料、权限和实际执行由业务代码校验。
