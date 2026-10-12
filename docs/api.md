# API 与权限协议

API 由 FastAPI 提供。启动应用后，`/docs` 是完整交互契约，`/openapi.json` 提供请求与响应 schema。本文索引来自当前源码的已注册路由；原产品规格中的规划接口不等于已实现接口。

## 文档导航

[agent.md](agent.md) · [api.md](api.md) · [architecture.md](architecture.md) · [data_pipeline.md](data_pipeline.md) · [deployment.md](deployment.md) · [evaluation_report.md](evaluation_report.md) · [rag.md](rag.md) · [testing.md](testing.md)

## 本文内容

- [注册、邀请与成员开通](#registration)
- [项目文档、任务与成果审批](#project-collaboration)

## 已注册接口索引

索引整理于 2026-10-12，按路径排序。字段、默认值和响应模型以运行中 OpenAPI 与对应源码为准。

| 方法 | 路径 | 源码入口 |
| --- | --- | --- |
| GET | `/api/approvals` | [main.py](../backend/main.py) · `list_approvals` |
| POST | `/api/approvals/{approval_id}/decision` | [main.py](../backend/main.py) · `decide` |
| GET | `/api/artifacts/{artifact_id}` | [delivery_routes.py](../backend/delivery_routes.py) · `download_artifact` |
| POST | `/api/auth/email-verification` | [onboarding.py](../backend/onboarding.py) · `request_verification` |
| POST | `/api/auth/invitations/{token}/accept` | [auth_routes.py](../backend/auth_routes.py) · `accept_invitation` |
| POST | `/api/auth/login` | [auth_routes.py](../backend/auth_routes.py) · `login` |
| POST | `/api/auth/logout` | [auth_routes.py](../backend/auth_routes.py) · `logout` |
| GET | `/api/auth/me` | [auth_routes.py](../backend/auth_routes.py) · `me` |
| POST | `/api/auth/password/forgot` | [auth_routes.py](../backend/auth_routes.py) · `forgot_password` |
| POST | `/api/auth/password/reset` | [auth_routes.py](../backend/auth_routes.py) · `reset_password` |
| POST | `/api/auth/platform-invitations/{token}/accept` | [auth_routes.py](../backend/auth_routes.py) · `accept_platform_invitation` |
| POST | `/api/auth/platform/login` | [auth_routes.py](../backend/auth_routes.py) · `platform_login` |
| GET | `/api/auth/profile` | [onboarding.py](../backend/onboarding.py) · `profile` |
| PATCH | `/api/auth/profile` | [onboarding.py](../backend/onboarding.py) · `update_profile` |
| POST | `/api/auth/profile/email` | [onboarding.py](../backend/onboarding.py) · `bind_email` |
| POST | `/api/auth/register` | [onboarding.py](../backend/onboarding.py) · `register` |
| GET | `/api/auth/spaces` | [onboarding.py](../backend/onboarding.py) · `spaces` |
| POST | `/api/auth/spaces/{space_id}/switch` | [onboarding.py](../backend/onboarding.py) · `switch_space` |
| POST | `/api/auth/verify-email` | [onboarding.py](../backend/onboarding.py) · `verify_email` |
| GET | `/api/chat/assistant` | [chat_routes.py](../backend/chat_routes.py) · `assistant_description` |
| GET | `/api/chat/conversations` | [chat_routes.py](../backend/chat_routes.py) · `conversations` |
| POST | `/api/chat/conversations` | [chat_routes.py](../backend/chat_routes.py) · `create` |
| DELETE | `/api/chat/conversations/{identifier}` | [chat_routes.py](../backend/chat_routes.py) · `delete_conversation` |
| GET | `/api/chat/conversations/{identifier}` | [chat_routes.py](../backend/chat_routes.py) · `get_conversation` |
| PATCH | `/api/chat/conversations/{identifier}` | [chat_routes.py](../backend/chat_routes.py) · `update_conversation` |
| GET | `/api/chat/conversations/{identifier}/context` | [chat_routes.py](../backend/chat_routes.py) · `get_context` |
| POST | `/api/chat/conversations/{identifier}/context/retry` | [chat_routes.py](../backend/chat_routes.py) · `retry_context` |
| POST | `/api/chat/conversations/{identifier}/documents` | [chat_routes.py](../backend/chat_routes.py) · `upload` |
| DELETE | `/api/chat/conversations/{identifier}/documents/{document_id}` | [chat_routes.py](../backend/chat_routes.py) · `delete_document` |
| GET | `/api/chat/conversations/{identifier}/messages` | [chat_routes.py](../backend/chat_routes.py) · `message_page` |
| POST | `/api/chat/conversations/{identifier}/messages` | [chat_routes.py](../backend/chat_routes.py) · `send` |
| GET | `/api/chat/memory` | [chat_routes.py](../backend/chat_routes.py) · `memory` |
| PUT | `/api/chat/memory` | [chat_routes.py](../backend/chat_routes.py) · `update_memory` |
| GET | `/api/chat/memory-candidates` | [memory_items.py](../backend/memory_items.py) · `candidates` |
| POST | `/api/chat/memory-candidates/{identifier}/{decision}` | [memory_items.py](../backend/memory_items.py) · `decide_candidate` |
| GET | `/api/chat/memory-items` | [memory_items.py](../backend/memory_items.py) · `list_items` |
| POST | `/api/chat/memory-items` | [memory_items.py](../backend/memory_items.py) · `create_item` |
| DELETE | `/api/chat/memory-items/{identifier}` | [memory_items.py](../backend/memory_items.py) · `delete_item` |
| PATCH | `/api/chat/memory-items/{identifier}` | [memory_items.py](../backend/memory_items.py) · `edit_item` |
| GET | `/api/chat/memory-preferences` | [memory_items.py](../backend/memory_items.py) · `preferences` |
| PUT | `/api/chat/memory-preferences` | [memory_items.py](../backend/memory_items.py) · `update_preferences` |
| POST | `/api/companies` | [onboarding.py](../backend/onboarding.py) · `create_company` |
| GET | `/api/companies/name-check` | [onboarding.py](../backend/onboarding.py) · `company_name_check` |
| GET | `/api/company/audit-events` | [admin_routes.py](../backend/admin_routes.py) · `list_audit_events` |
| GET | `/api/company/audit-events/export.csv` | [admin_routes.py](../backend/admin_routes.py) · `export_audit_events` |
| GET | `/api/company/company-directory` | [admin_routes.py](../backend/admin_routes.py) · `company_directory` |
| GET | `/api/company/dashboard` | [admin_routes.py](../backend/admin_routes.py) · `dashboard` |
| GET | `/api/company/exports/tenant` | [admin_routes.py](../backend/admin_routes.py) · `export_tenant_data` |
| POST | `/api/company/invitations` | [admin_routes.py](../backend/admin_routes.py) · `create_invitation` |
| DELETE | `/api/company/invitations/{invitation_id}` | [admin_routes.py](../backend/admin_routes.py) · `delete_invitation` |
| POST | `/api/company/invitations/{invitation_id}/resend` | [admin_routes.py](../backend/admin_routes.py) · `resend_invitation` |
| POST | `/api/company/invitations/{invitation_id}/revoke` | [admin_routes.py](../backend/admin_routes.py) · `revoke_invitation` |
| GET | `/api/company/member-imports/template.csv` | [member_import_routes.py](../backend/member_import_routes.py) · `template` |
| POST | `/api/company/member-imports/validate` | [member_import_routes.py](../backend/member_import_routes.py) · `validate` |
| GET | `/api/company/member-imports/{batch_id}` | [member_import_routes.py](../backend/member_import_routes.py) · `get_batch` |
| POST | `/api/company/member-imports/{batch_id}/commit` | [member_import_routes.py](../backend/member_import_routes.py) · `commit_batch` |
| GET | `/api/company/members` | [admin_routes.py](../backend/admin_routes.py) · `list_members` |
| PATCH | `/api/company/members/{membership_id}` | [admin_routes.py](../backend/admin_routes.py) · `update_member` |
| POST | `/api/company/members/{membership_id}/password-reset` | [admin_routes.py](../backend/admin_routes.py) · `member_password_reset` |
| GET | `/api/company/projects` | [admin_routes.py](../backend/admin_routes.py) · `admin_projects` |
| GET | `/api/company/projects/trash` | [admin_routes.py](../backend/admin_routes.py) · `project_trash` |
| DELETE | `/api/company/projects/{project_id}/purge` | [admin_routes.py](../backend/admin_routes.py) · `purge_project` |
| POST | `/api/company/projects/{project_id}/restore` | [admin_routes.py](../backend/admin_routes.py) · `restore_project` |
| GET | `/api/company/support-access-grants` | [support_routes.py](../backend/support_routes.py) · `list_support_access` |
| POST | `/api/company/support-access-grants/{grant_id}/approve` | [support_routes.py](../backend/support_routes.py) · `approve_support_access` |
| POST | `/api/company/support-access-grants/{grant_id}/reject` | [support_routes.py](../backend/support_routes.py) · `reject_support_access` |
| POST | `/api/company/support-access-grants/{grant_id}/revoke` | [support_routes.py](../backend/support_routes.py) · `company_revoke_support_access` |
| GET | `/api/company/tenants` | [admin_routes.py](../backend/admin_routes.py) · `list_tenants` |
| POST | `/api/imports/validate` | [main.py](../backend/main.py) · `validate_import` |
| GET | `/api/imports/{job_id}` | [delivery_routes.py](../backend/delivery_routes.py) · `import_details` |
| GET | `/api/imports/{job_id}/errors.csv` | [delivery_routes.py](../backend/delivery_routes.py) · `import_errors` |
| POST | `/api/imports/{job_id}/execute` | [main.py](../backend/main.py) · `execute_import` |
| POST | `/api/imports/{job_id}/retry-failed` | [delivery_routes.py](../backend/delivery_routes.py) · `retry_failed_rows` |
| GET | `/api/knowledge` | [knowledge_routes.py](../backend/knowledge_routes.py) · `list_documents` |
| POST | `/api/knowledge` | [knowledge_routes.py](../backend/knowledge_routes.py) · `upload_document` |
| POST | `/api/knowledge/files` | [knowledge_routes.py](../backend/knowledge_routes.py) · `upload_file` |
| POST | `/api/knowledge/inspect` | [knowledge_routes.py](../backend/knowledge_routes.py) · `inspect_rag` |
| GET | `/api/knowledge/{document_id}` | [knowledge_routes.py](../backend/knowledge_routes.py) · `read_document` |
| POST | `/api/knowledge/{document_id}/deactivate` | [knowledge_routes.py](../backend/knowledge_routes.py) · `deactivate` |
| POST | `/api/knowledge/{document_id}/reindex` | [knowledge_routes.py](../backend/knowledge_routes.py) · `reindex` |
| DELETE | `/api/mcp` | [chat_mcp.py](../backend/chat_mcp.py) · `unsupported` |
| GET | `/api/mcp` | [chat_mcp.py](../backend/chat_mcp.py) · `unsupported` |
| POST | `/api/mcp` | [chat_mcp.py](../backend/chat_mcp.py) · `rpc` |
| GET | `/api/platform/audit-events` | [platform_routes.py](../backend/platform_routes.py) · `global_audit` |
| GET | `/api/platform/dashboard` | [platform_routes.py](../backend/platform_routes.py) · `dashboard` |
| GET | `/api/platform/evaluations` | [platform_routes.py](../backend/platform_routes.py) · `list_evaluations` |
| POST | `/api/platform/evaluations/run` | [platform_routes.py](../backend/platform_routes.py) · `run_evaluation` |
| GET | `/api/platform/evaluations/{evaluation_id}/download` | [platform_routes.py](../backend/platform_routes.py) · `download_evaluation` |
| GET | `/api/platform/inspect/projects/{project_id}` | [platform_routes.py](../backend/platform_routes.py) · `inspect_project` |
| GET | `/api/platform/inspect/tenants/{tenant_id}/projects` | [platform_routes.py](../backend/platform_routes.py) · `inspect_projects` |
| GET | `/api/platform/retrieval/status` | [retrieval_status.py](../backend/retrieval_status.py) · `status` |
| GET | `/api/platform/staff` | [platform_routes.py](../backend/platform_routes.py) · `list_staff` |
| POST | `/api/platform/staff/invitations` | [platform_routes.py](../backend/platform_routes.py) · `invite_staff` |
| GET | `/api/platform/support-access-grants` | [support_routes.py](../backend/support_routes.py) · `list_platform_support_access` |
| POST | `/api/platform/support-access-grants` | [support_routes.py](../backend/support_routes.py) · `create_support_access` |
| GET | `/api/platform/support-access-grants/targets/{tenant_id}` | [support_routes.py](../backend/support_routes.py) · `support_targets` |
| POST | `/api/platform/support-access-grants/{grant_id}/revoke` | [support_routes.py](../backend/support_routes.py) · `platform_revoke_support_access` |
| GET | `/api/platform/system-status` | [platform_routes.py](../backend/platform_routes.py) · `system_status` |
| GET | `/api/platform/tenants` | [platform_routes.py](../backend/platform_routes.py) · `list_tenants` |
| POST | `/api/platform/tenants` | [platform_routes.py](../backend/platform_routes.py) · `create_tenant` |
| PATCH | `/api/platform/tenants/{tenant_id}` | [platform_routes.py](../backend/platform_routes.py) · `update_tenant` |
| GET | `/api/platform/users` | [platform_routes.py](../backend/platform_routes.py) · `list_users` |
| PATCH | `/api/platform/users/{user_id}` | [platform_routes.py](../backend/platform_routes.py) · `update_user` |
| POST | `/api/platform/users/{user_id}/revoke-sessions` | [platform_routes.py](../backend/platform_routes.py) · `revoke_user_sessions` |
| POST | `/api/platform/users/{user_id}/transfer-company` | [platform_routes.py](../backend/platform_routes.py) · `transfer_company` |
| POST | `/api/project-briefs/check` | [tool_routes.py](../backend/tool_routes.py) · `check` |
| GET | `/api/project-collaborations/pending` | [project_access_routes.py](../backend/project_access_routes.py) · `pending_collaborations` |
| DELETE | `/api/project-collaborations/{collaboration_id}` | [project_access_routes.py](../backend/project_access_routes.py) · `revoke_collaboration` |
| POST | `/api/project-collaborations/{collaboration_id}/accept` | [project_access_routes.py](../backend/project_access_routes.py) · `accept_collaboration` |
| POST | `/api/project-collaborations/{collaboration_id}/reject` | [project_access_routes.py](../backend/project_access_routes.py) · `reject_collaboration` |
| GET | `/api/projects` | [main.py](../backend/main.py) · `list_projects` |
| POST | `/api/projects` | [main.py](../backend/main.py) · `create_project` |
| DELETE | `/api/projects/{project_id}` | [main.py](../backend/main.py) · `delete_project` |
| GET | `/api/projects/{project_id}` | [main.py](../backend/main.py) · `get_project` |
| PATCH | `/api/projects/{project_id}` | [delivery_routes.py](../backend/delivery_routes.py) · `revise_project` |
| GET | `/api/projects/{project_id}/collaborating-companies` | [project_access_routes.py](../backend/project_access_routes.py) · `list_collaborations` |
| POST | `/api/projects/{project_id}/collaborating-companies` | [project_access_routes.py](../backend/project_access_routes.py) · `invite_collaborating_company` |
| GET | `/api/projects/{project_id}/delivery` | [delivery_routes.py](../backend/delivery_routes.py) · `delivery_details` |
| POST | `/api/projects/{project_id}/feedback` | [delivery_routes.py](../backend/delivery_routes.py) · `submit_feedback` |
| GET | `/api/projects/{project_id}/go-live-report` | [main.py](../backend/main.py) · `report` |
| POST | `/api/projects/{project_id}/imports/preview` | [tool_routes.py](../backend/tool_routes.py) · `import_preview` |
| PATCH | `/api/projects/{project_id}/materials` | [delivery_routes.py](../backend/delivery_routes.py) · `revise_materials` |
| GET | `/api/projects/{project_id}/members` | [project_access_routes.py](../backend/project_access_routes.py) · `list_project_members` |
| POST | `/api/projects/{project_id}/members` | [project_access_routes.py](../backend/project_access_routes.py) · `add_project_member` |
| DELETE | `/api/projects/{project_id}/members/{membership_id}` | [project_access_routes.py](../backend/project_access_routes.py) · `remove_project_member` |
| PATCH | `/api/projects/{project_id}/members/{membership_id}` | [project_access_routes.py](../backend/project_access_routes.py) · `update_project_member` |
| GET | `/api/projects/{project_id}/members/{membership_id}/capability-grants` | [project_access_routes.py](../backend/project_access_routes.py) · `list_capability_grants` |
| POST | `/api/projects/{project_id}/members/{membership_id}/capability-grants` | [project_access_routes.py](../backend/project_access_routes.py) · `create_capability_grant` |
| DELETE | `/api/projects/{project_id}/members/{membership_id}/capability-grants/{grant_id}` | [project_access_routes.py](../backend/project_access_routes.py) · `revoke_capability_grant` |
| GET | `/api/projects/{project_id}/plan` | [main.py](../backend/main.py) · `project_plan` |
| POST | `/api/projects/{project_id}/runs` | [main.py](../backend/main.py) · `start_run` |
| GET | `/api/projects/{project_id}/tasks` | [project_task_routes.py](../backend/project_task_routes.py) · `list_tasks` |
| POST | `/api/projects/{project_id}/tasks` | [project_task_routes.py](../backend/project_task_routes.py) · `create_task` |
| PUT | `/api/projects/{project_id}/tasks/{task_id}` | [project_task_routes.py](../backend/project_task_routes.py) · `update_task` |
| POST | `/api/projects/{project_id}/tasks/{task_id}/actions/{action}` | [project_task_routes.py](../backend/project_task_routes.py) · `task_action` |
| POST | `/api/projects/{project_id}/tasks/{task_id}/reviews/{review_id}/decision` | [project_task_routes.py](../backend/project_task_routes.py) · `decide_task_review` |
| GET | `/api/role-catalog` | [project_access_routes.py](../backend/project_access_routes.py) · `role_catalog` |
| GET | `/api/runs/{run_id}` | [main.py](../backend/main.py) · `get_run` |
| GET | `/api/runs/{run_id}/actions` | [main.py](../backend/main.py) · `get_run_actions` |
| POST | `/api/runs/{run_id}/cancel` | [main.py](../backend/main.py) · `cancel_run` |
| PATCH | `/api/runs/{run_id}/configuration` | [delivery_routes.py](../backend/delivery_routes.py) · `revise_configuration` |
| GET | `/api/runs/{run_id}/events` | [main.py](../backend/main.py) · `run_events` |
| POST | `/api/runs/{run_id}/resume` | [main.py](../backend/main.py) · `resume_run` |
| GET | `/api/runs/{run_id}/snapshots` | [main.py](../backend/main.py) · `get_run_snapshots` |
| GET | `/api/runs/{run_id}/snapshots/{snapshot_id}` | [main.py](../backend/main.py) · `get_run_snapshot` |
| GET | `/api/runs/{run_id}/steps` | [main.py](../backend/main.py) · `get_run_steps` |
| GET | `/api/tasks` | [main.py](../backend/main.py) · `my_tasks` |
| GET | `/health` | [main.py](../backend/main.py) · `health` |
| GET | `/metrics` | [main.py](../backend/main.py) · `metrics` |
| GET | `/ready` | [main.py](../backend/main.py) · `ready` |

## 通用请求约束

- 客户与平台身份入口分离；资源访问仍需公司、项目及操作权限。
- 登录会话使用 Cookie；受保护的写请求需要 `X-CSRF-Token`，业务幂等入口还需 `Idempotency-Key`。具体豁免与参数见源码。
- 业务响应通常封装 `data`、`request_id`、`trace_id`；SSE 和下载接口遵循各自媒体类型。
- 常见状态：401 未认证、403 权限或审批不足、404 不存在或不可见、409 状态/版本冲突、422 参数错误。
- 审批与恢复必须校验材料和版本；模型建议、页面入口及历史文档不会授予额外权限。


<a id="registration"></a>

## 注册、邀请与成员开通

SaaS 实施平台 面向所有用户开放现有 SaaS 实施服务。个人可以自行注册和使用；企业管理员可以自行创建公司、导入成员并管理公司项目。不包含通用 SaaS 连接器、订阅收费、企业实名认证或手机号登录。

### 使用流程

1. 从登录页进入“创建账号”，填写独立账号和密码，立即创建账号及个人空间。账号为 3–40 位，以字母开头，支持字母、数字、点、下划线及连字符，统一转小写。登录后在“个人主页”设置昵称、绑定邮箱和填写手机号。
2. 在个人主页验证邮箱后，可在“空间与账号”创建公司。公司创建人获得公司管理员身份。同名公司会提示确认，空间标识必须唯一；`personal-` 为系统保留前缀。
3. 从公司设置的“成员与邀请”进入批量导入。下载 CSV 模板，填写姓名、邮箱、部门及选填工号，上传后校验，再确认提交。
4. 新成员通过 48 小时有效的链接设置账号；已有账号须先登录对应邮箱，再确认加入。加入公司不会修改其全局姓名、密码或个人项目。
5. 激活状态、邀请撤销和重发在公司设置查看和操作。重发使旧链接失效；撤销后不能激活。

每个客户账号拥有一个个人空间，最多一条有效企业成员关系。加入公司不会转移个人项目。切换空间会轮换会话、刷新页面，并通知同浏览器的其他页面刷新，清除旧空间缓存和 SSE 连接。

邮箱验证链接 1 小时有效，只能由申请绑定的账号登录后使用。邮箱绑定完成前不写入账号联系方式；已有邮箱保留，当前入口不支持替换已有邮箱。历史用户仍可用原邮箱及密码登录，在个人主页查看或修改补建的独立账号。

手机号暂不发送验证码，保存时检查格式并判重，中国大陆号码自动添加 +86，保持“未验证”状态，不用于登录或找回密码；清空可移除。修改账号及手机号需要当前密码。未绑定邮箱不影响个人空间使用，但无法通过邮箱找回密码。

### 重复核验规则

- CSV 为 UTF-8，允许 BOM；最多 2 MB、1,000 条记录。
- 邮箱去首尾空白并转小写，账号邮箱具有数据库规范化唯一索引。姓名和部门不判重。
- 工号去首尾空白，保留前导零，区分大小写；空工号不参与唯一判断。
- 文件内邮箱或工号重复为错误；工号在公司目录内唯一，目录统一保存待激活及已激活资料。
- 本公司已有成员或待激活记录按邮箱跳过，不覆盖资料、不重发邮件；资料不一致时报冲突。已有旧记录未设工号时，不能通过重复导入补写工号。
- 已有个人账号可加入；已归属其他企业、平台账号、停用或删除账号不可加入。响应不披露另一家企业的信息。
- 存在错误时整批阻止提交。提交时重新校验，激活时重新检查账号及企业归属，数据库约束处理并发竞争。
- 同批次重复提交不重复建立邀请、目录记录或邮件。不同批次导入已存在邮箱时跳过。已过期邀请仍在列表中，管理员可重发或撤销。
- 撤销或删除未激活邀请后，没有成员关系或其他待激活邀请的目录占位会释放；已激活成员资料保留。

公司内导入姓名保存在公司目录，展示在公司成员列表；不覆盖已有账号的全局姓名。公司目录中的部门和工号属于该公司。

### 接口

| 接口 | 行为 |
|---|---|
| `POST /api/auth/register` | 账号注册，输入 `username`、`password`，可选 `display_name`；保留旧邮箱注册接口兼容 |
| `GET /api/auth/profile` | 查看自己的账号和联系方式 |
| `PATCH /api/auth/profile` | 修改昵称、账号或手机号；修改账号或手机号需 `current_password` |
| `POST /api/auth/profile/email` | 输入 `email`、`current_password`，发送绑定验证邮件 |
| `POST /api/auth/verify-email` | 消费一次性 `token`；新用户提交 `display_name`、`password`，已有用户先登录 |
| `POST /api/auth/email-verification` | 登录用户请求验证自己的邮箱 |
| `GET /api/auth/spaces` | 当前用户可用空间列表 |
| `POST /api/auth/spaces/{id}/switch` | 验证成员关系并切换、轮换 Cookie |
| `GET /api/companies/name-check?name=...` | 只返回是否存在同名公司，不返回内部资料 |
| `POST /api/companies` | 已验证客户账号创建公司，输入 `name`、`slug` |
| `GET /api/company/member-imports/template.csv` | 下载模板 |
| `POST /api/company/member-imports/validate` | 提交 `csv_text`，返回批次 ID、逐行结果及新增/跳过数量 |
| `POST /api/company/member-imports/{id}/commit` | 要求 `Idempotency-Key`，重新校验并原子提交 |
| `GET /api/company/member-imports/{id}` | 在本公司权限范围查询批次结果 |

`/api/auth/me` 增加 `workspace_kind`、`email_verified`，`memberships` 的每个空间增加 `kind`。已有客户和平台登录接口保留。已有用户接受公司邀请可提交空对象，但必须登录对应账号并提供 CSRF 头；新用户仍需姓名及密码。

### 权限和审批

个人项目允许空间所有者执行实施流程和本人确认四类审批，审计记录包含 `personal_confirmation`。规则由服务端根据项目实际所属空间判断。个人项目不能绑定其他企业或添加企业协作；个人空间不能邀请公司成员或修改其所有者身份。

企业项目继续使用现有项目角色、协作授权和发起人与审批人分离规则。平台工作人员仍使用独立账号和入口，公开注册不能获取平台权限。

### 发布与迁移

停止旧版 API 写入并备份数据库，使用迁移账号运行 `alembic upgrade head`，再启动新 API 和 Web。新增迁移为 `0014_open_registration`、`0015_username_profile`，应用数据库角色需获得新增表 DML 权限（沿用 `python -m backend.db_role grant`）。

迁移前检查规范化邮箱冲突和同公司待激活邀请冲突，报告冲突记录 ID 后中止，不自动合并。历史表没有工号，新公司目录通过唯一约束禁止重复工号。历史客户账号补建个人空间，平台账号不补建；历史账号的邮箱验证时间保持为空。历史项目、成员和角色保持原归属。

本次迁移没有自动降级删除个人空间的实现。若需回滚，恢复发布前的数据库备份与旧应用版本，避免删除新产生的个人数据。

公开部署使用 `MAIL_DEBUG=false` 及已有 SMTP、邮件 Worker 配置。验证令牌只以哈希保存；待发邮件事务入队，邮件发送按现有 Worker 重试。调试链接只在 `MAIL_DEBUG=true` 时返回；批次和幂等结果不保存明文激活链接。

验证命令：`pytest -q`、`ruff check backend tests`、`mypy backend`，以及 Web 的类型检查与构建。设置 `RUN_BROWSER_TESTS=1` 后可运行浏览器测试；`tests/test_browser_onboarding.py` 验证真实页面的注册、创建公司、导入和空间切换。可设置 `PLAYWRIGHT_CHANNEL=msedge` 使用本机 Edge。

独立 PostgreSQL 验证脚本为 `tests/check_registration_postgres.py`：先迁移专用的临时 `registration_test` 数据库，再以 `REGISTRATION_TEST_POSTGRES_URL` 指向其管理连接执行脚本。脚本创建无超级权限的应用测试角色，验证注册、跨公司并发激活、目录 RLS 和个人完整工作流。本次已在 PostgreSQL 16 隔离容器通过这些检查；正式邮件投递和实际生产环境发布仍需按部署流程验收。

<a id="project-collaboration"></a>

## 项目文档、任务与成果审批

项目详情提供「项目文档」和「协作任务」两个入口。

- 项目经理、实施顾问、审批人、客户联系人和有项目访问权的公司管理员可提交文档、创建与更新协作任务。只读成员保留浏览能力。
- 文档支持直接填写正文或上传 PDF（最大 10 MiB）、PPTX/XLSX（最大 5 MiB）、DOCX/Markdown/TXT/CSV/JSON（最大 2 MiB）。上传文件沿用 知识检索 后台解析和索引服务，需运行对应 worker；填写正文后可立即查阅。PDF 扫描页由后台 OCR 处理，限制和告警见 [PDF 与 Office 知识文档](data_pipeline.md#document-formats)。文档记录提交人，同一公司内同标题版本需递增，项目范围不能变更。
- 有效的项目协作公司成员可以查阅项目文档和任务；公司通用文档继续仅向本公司开放。撤销项目成员或公司协作后，原成员失去该项目的访问权限。
- 每个项目可建立多个任务，设置名称、说明、负责人和截止日期。状态不能手动选择，而是按“待开始 → 进行中 → 提交任务文档 → 待审批 → 已通过”推进；驳回后进入“需修改”，再次提交会产生新的审批轮次。
- 每个任务在发起审批前至少要关联一份任务文档。审批记录会保存当轮文档 ID、提交人、审批人和意见；除个人空间所有者确认外，提交人不能审批自己的任务。
- “开始、报告阻塞、恢复”属于工作过程动作，不要求审批；只有交付结果需要审批。这样既保留完成状态的可信度，也避免为日常过程制造无意义审批。
- 已移除负责人的历史任务会保留，可重新分配。多人同时保存同一任务时，旧版本返回 409，刷新列表并重新打开任务后再编辑。
- 完成、归档或取消的项目禁止新增文档和编辑任务。任务完成比例不会自动触发项目上线验收，也不会改变项目生命周期；协作任务独立于实施 Agent Run，不改变单项目单活动 Run 的约束。

### 升级

停止服务并备份数据库后运行 `.venv\Scripts\python.exe -m alembic upgrade head`，重新构建前端并重启服务。本地 `start.ps1` 启动流程会执行数据库迁移。

迁移 `0028_project_collaboration` 新建任务表；迁移 `0029_task_review_workflow` 增加任务文档关联、审批记录和受控状态流转。数据库升级后再运行新版本服务。

### API

- `GET /api/projects/{project_id}/tasks`：返回任务列表及可分配成员。
- `POST /api/projects/{project_id}/tasks`：创建任务。
- `PUT /api/projects/{project_id}/tasks/{task_id}`：提交完整任务字段和 `expected_version`。
- `POST /api/knowledge`、`POST /api/knowledge/files`：携带 `project_id` 提交项目文档；不携带时仍要求公司管理员。

所有写请求均沿用现有会话和 CSRF 校验；创建和更新会写入审计日志。
