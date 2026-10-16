# SQLite 告警修复与验证

验证日期：2026-10-16。结果：原有 4 条 SQLite 表达式索引反射告警已消除，未使用忽略告警或放宽约束的方法。37 项定向回归通过，0 失败、0 跳过、0 告警；真实 PostgreSQL 迁移与安全检查通过。

## 原因与修复

原有两个测试在 Alembic batch 重建 `knowledge_documents` / `project_tasks` 时，沿外键递归反射 `users`。SQLite 的 SQLAlchemy 反射不能表示 `lower(trim(email))` 和 `lower(trim(username))`，因此每个测试各报告两个索引告警。这些子表迁移本身不需要读取 users 的索引。

0028、0029 及测试旧表准备过程使用 `reflect_kwargs={"resolve_fks": False}`，保留目标表的列、约束、索引和外键定义，避免加载无关表。继续在完整迁移链启用严格检查后，也定位并修复 0014 的递归 Table 读取，以及 0016、0018、0019、0030 的相关 batch 路径。

0015 与子表不同，确实需要重建 users。为避免反射丢失表达式索引，该迁移通过 `sqlite_master` 和 `PRAGMA index_xinfo` 找出表达式索引，在 SAVEPOINT 内保存原始 DDL、移除待随表重建的索引、替换表，再执行原始 DDL。大小写、trim、自定义表达式、部分索引条件及 UNIQUE 属性均按原定义恢复。表替换或索引恢复失败时，SAVEPOINT 回滚整个重建及索引变更。PostgreSQL 沿用其原生反射与 ALTER 路径。

没有修改全局 SQLite 方言、覆盖 SQLAlchemy 反射实现、使用当前 ORM 模型代替历史表结构，或添加忽略告警的过滤器。CI 增加 `-W error::sqlalchemy.exc.SAWarning`，新迁移链测试在子解释器内也显式将 SAWarning 作为错误。

## 验证结果

| 范围 | 结果 |
|---|---|
| 原两个迁移测试 | 严格告警模式通过，原 4 条告警不再出现 |
| 全部 9 个迁移测试文件、账号资料、安全修复、就绪检查 | 37 项通过，182.47 秒，0 告警 |
| SQLite 实际 Alembic 链 | 空库升级至 0034 head，带数据回退至 0027，再升级 head；每个子进程将 SAWarning 作为错误，通过 |
| 0028 / 0029 历史表升级、回退、再次升级 | 逐阶段核对用户索引原始 SQL；原文、任务、外键定义保留；旧 done 与审批状态转换符合原迁移规则 |
| 唯一索引实际执行 | INSERT 和 UPDATE 的大小写及首尾空格重复邮箱、账号均被数据库拒绝；允许多个 NULL 联系方式 |
| 自定义索引 | 自定义表达式部分唯一索引原始 SQL 完全相同，重复值实际被拒绝 |
| 故障注入 | 空表/有数据分别在替换表、恢复索引阶段中断，原索引及身份保留，无遗留临时表 |
| SQLite 外键 | 升级/回退各阶段 `foreign_key_check` 无异常；启用外键后，无效文档/任务引用实际被拒绝 |
| 真实 PostgreSQL | 独立临时 pgvector/pg16：fresh head → 0027 → head；索引 DDL、原文及唯一性检查通过 |
| PostgreSQL 安全 | 非所有者 NOBYPASSRLS 共享读取、私有隔离、外来写入拒绝、上传/审批交错、并发重置令牌、撤销协作、过期项目清理均通过 |
| 静态检查 | 全仓 ruff、git diff --check 通过 |

37 项构成：BGE 迁移 4、上下文迁移 3、执行迁移 1、账号迁移 7、注册迁移 2、项目协作迁移 1、任务审批迁移 1、安全迁移 1、完整 SQLite 链 1、账号资料 4、安全修复 10、就绪 2。此次没有重新运行上一轮全部 597 项；本轮定向结果不与此前测试数累加。

本次仅更改迁移反射、索引重建保护及测试门禁，未增加数据库 revision。现有业务库没有执行回退，也无需重新执行已完成的历史迁移。PostgreSQL 验证使用独立临时容器，已确认清理，不修改现有业务数据。本轮未重新发布运行容器；修正后的迁移文件随下一次镜像构建进入服务。

## 可追溯证据

- `evidence/before.log`：修复前，两个测试在 SAWarning 作为错误时失败，记录递归反射调用栈；另有本机旧 pytest 缓存目录写权限提示，最终使用独立可写缓存目录解决，未过滤该提示。
- `evidence/fresh-before.log`、`fresh-pass1.log`、`fresh-pass2.log`：完整链逐步定位到 0014、0015、0016；失败记录保留。
- `evidence/fresh-pass3.log`：修正后空库严格升级成功。
- `evidence/regression.xml` / `regression.log`：最终 37 项测试，命令使用 `-W error`。
- `evidence/postgres-migrations.log`、`postgres-security.log`、`postgres.json`：实际 PostgreSQL 迁移、安全与临时容器清理结果。
- `check_postgres.py`：本轮可重复执行的隔离 PostgreSQL 验证脚本，生成凭据不会输出到报告。

本报告补充上一轮全量回归报告；旧 JUnit 中的 4 条告警仍保留为历史证据。
