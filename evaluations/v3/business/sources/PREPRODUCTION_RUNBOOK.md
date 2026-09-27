# 隔离预生产运行与恢复

此环境名为 saas-preproduction，使用独立数据库卷与 18xxx/19xxx 本机端口。不是生产发布配置；HTTP 仅绑定回环地址。远程开放前必须加 HTTPS、安全 Cookie 和网络访问控制。

## 启动

需要可运行 Linux 容器的 Docker Engine。本机主 Compose 已完成备份、迁移和业务冒烟验收；下述隔离预生产 Compose 及其独立卷、S3 恢复和故障演练仍需单独执行与记录。

```powershell
.venv\Scripts\python.exe scripts/init_preproduction.py
docker compose --env-file .env.preproduction -f docker-compose.preproduction.yml config --quiet
docker compose --env-file .env.preproduction -f docker-compose.preproduction.yml up --build -d
docker compose --env-file .env.preproduction -f docker-compose.preproduction.yml ps
```

生成器不覆盖已有 Secret，不在日志显示密码；.env.preproduction 用于迁移与 Compose，.env.preproduction.runtime 不含数据库 owner 连接。文件均被 Git 忽略；仅在本机读取管理员密码，不粘贴至聊天或报告。

入口：Web http://localhost:18080；平台登录 /platform/login；Mailpit http://localhost:18025；Grafana http://localhost:13000；Prometheus http://localhost:19090。

迁移容器先确保应用数据库角色，再升级 Alembic、初始化 LangGraph 检查点并授予 DML。API/Worker 不执行 DDL。模型默认 deterministic；实际模型运行需在 runtime 文件设置 MODEL_MODE=live、MODEL_BASE_URL、MODEL_NAME、MODEL_API_KEY 和 EMBEDDING_MODEL，并重启相关容器。不要将凭据提交版本库。

## 多角色验收

1. 平台管理员创建测试公司，从 Mailpit 取得首位公司管理员邀请。
2. 公司管理员邀请实施顾问、独立审批人、客户联系人；创建 80 人项目，明确部门与迁移范围。
3. 公司管理员启动 Run，独立审批人审核计划；实施顾问可在配置审批前修订方案，原批准材料不得复用。
4. 配置批准后，顾问上传 80 人 CSV，包含一名 admin；部门须与目标配置一致。
5. 独立审批人批准导入，核对实际成员与交付物；客户联系人提交反馈，独立审批人完成验收。
6. 重复上传/跨项目审批/未授权访问必须失败；人数不足时必须阻塞项目，不能关闭。

## 备份与恢复

backup 容器每天创建整库 custom-format dump，并检查可读取目录。输出到 backups/preproduction，last_success.txt 记录最后成功时间。当前不自动删除历史备份，也不提供 PITR。

恢复演练只创建新的 saas_restore_<timestamp> 数据库，不覆盖源库：

```sh
docker compose --env-file .env.preproduction -f docker-compose.preproduction.yml exec \
  -e BACKUP_FILE=/backups/saas_preproduction_<timestamp>.dump backup \
  sh /scripts/restore_preproduction.sh
```

在无写入的样例环境比对项目、Run、步骤、成员和交付物数量；同时下载并校验 S3 对象。PostgreSQL dump 不包含 S3 对象，需另外备份 artifacts 卷。记录备份时间、恢复耗时、比对结果；未完成这些步骤不能声明 RPO≤24h/RTO≤4h 达标。

## 故障与观测

- 在测试 Run 执行期间重启 worker，核对步骤序号、审批材料和成员唯一约束；Outbox 同一事件重复投递不得重复写入。
- 停止 Redis 后 API 应保留 Outbox；恢复后重新投递。终止任务有明确失败类型，依赖错误最多自动尝试三次。
- SMTP 投递采用 at-least-once；极端断线可能重复发送同 Message-ID 的邮件，但一次性链接只能使用一次。
- 检查 Prometheus 告警和 Grafana 面板。当前 OTel Collector 输出基础调试 Trace；跨 Worker Trace 存储与外部通知接收器须另行验证。
- 如果执行人权限在排队期间被撤销，禁止 Worker 提升权限继续执行；检查 Outbox 错误并由有权限的项目经理取消/重试。

## 兼容与回退

旧导入任务的 run_id 保持 NULL 供历史查询，不能冒用新审批执行；须新建 Run 重新校验。新旧待审批材料契约不兼容，升级前完成或取消旧的待审批 Run，不自动追认旧批准。

迁移 0010–0013 是扩展式升级，降级会删除本轮新增数据表；先备份并确保无等待材料或未投递任务，优先恢复备份到隔离库验证，不在真实环境直接降级。

平台离线评测执行接口现要求 `Idempotency-Key`；相同账号的重复请求键返回原报告。Trace 页面显示持久化节点事件，不提供未采集的耗时或跨服务 Span，不替代 Collector/Worker 运行验证。
