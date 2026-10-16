# NLTK 告警修复与验证

2026-10-15；工作目录：`D:\Program Data\CursorProjects\SaaS_Agent`。

## 修复结果

已从 `benchmark`、`dev` 依赖和本机 `.venv` 移除 NLTK。此次处理没有忽略安全编号或修改包版本来隐藏告警。

此前依赖审计记录的 `PYSEC-2026-3740`（CVE-2026-81726 / GHSA-8mgp-746c-j5xp）涉及模型文件接口绕过路径沙箱。原文档称其为“下载器告警”不准确，已更正。查询时 [PyPI 最新发布版](https://pypi.org/project/nltk/) 仍为 3.10.3，原审计未提供修复版本；[上游安全修复跟踪](https://github.com/nltk/nltk/issues/3793) 不能等同于已发布的修复版。

项目实际只需要 `nltk.word_tokenize` 的默认英文分句与分词语义。保留的推理代码来自官方 NLTK 3.10.3 wheel，并逐文件核对原始 SHA-256；只提取英文 Punkt 推理及改进的 Treebank 分词规则。未保留训练、模型文件读写、pickle、下载器或外部语料发现功能。代码归属与 Apache 2.0 许可证保存在 `backend/_vendor/cmrc_tokenizer/`。英文参数从固定提交的官方 NLTK 数据包提取，逐字节确认与旧评测参数一致，保留原模型说明与来源。

`backend/benchmark_tokenizer.py` 只读取安装包中四个固定名称的 UTF-8 参数文件，每次首次加载校验 SHA-256；不接受用户路径，不读取 `NLTK_DATA`，无需网络或额外下载。文件损坏直接使评分失败，不偷偷改用其他分词算法。

## 兼容性

- 保留 `pip install -e '.[benchmark]'` 安装命令，extra 现在不再引入额外依赖。
- 保留原 CMRC 标点归一化、最长连续匹配、多参考答案取最佳、失败题计零分、证据位置匹配、分母、计费和报告不可覆盖规则。
- JSON 报告保留 `scoring.nltk_version` 键，值改为 `null`；新增 `scoring.tokenizer`，记录实现、参考版本和固定参数摘要。未宣称安装了完整 NLTK。
- 现有 `prepare/run/score` 命令及 Docker 采集脚本仍可使用。CLI 评分无需旧数据目录；JSON、Markdown 和预测文件均继续生成。
- pip 更新依赖不会自动卸载旧包。本机已先重新安装 `.[dev,benchmark]` 再卸载 NLTK，并通过 `pip check`。

## 对照验证

先在旧实现和 NLTK 3.10.3 仍存在时保存基线，再卸载依赖后比较，未改写原语料、冻结运行、历史预测及旧报告。

| 检查 | 结果 |
|---|---|
| 公开开发集 | 全部 3,219 题的问句、参考答案、848 段原文，并加入历史预测、英文边界例和固定随机组合 |
| 文本比较 | 8,887 个去重文本，分别比较直接英文分词与 CMRC 混合分词，共 17,774 次比较，0 差异 |
| 评分比较 | 12,876 组参考答案/预测组合，EM/F1 均完全一致 |
| 历史运行重算 | 原 200 题逐题结果、汇总指标、证据命中、延迟、usage、错误状态和其他原字段完全一致；仅评分实现元数据更新 |
| 原 EM / 新 EM | 70.5% / 70.5% |
| 原 F1 / 新 F1 | 87.62201353576889% / 87.62201353576889% |
| wheel 验证 | 在 `python -I -S` 的隔离子进程直接从 wheel 导入并评分，禁止依赖项目目录或 site-packages，确认无 NLTK，参数完整 |
| 安全加载回归 | 覆盖禁止导入 NLTK、禁网、无效外部数据目录、参数篡改拒绝和资源路径越界拒绝 |
| 自动化回归 | 首轮 89 项通过；新增 CLI 用例并整理推理代码格式后，相关 36 项再次通过。按测试节点去重共 90 项通过，0 失败，未将两轮数量直接相加 |
| 依赖审计 | 本机完整环境 0 项已知漏洞；145 项清单中 144 个第三方依赖可审计，项目自身因未发布 PyPI 被工具标注跳过 |
| 静态检查 | ruff 与 mypy 通过；pip check 无依赖冲突 |

历史 200 题记录原本包含失败，评分 CLI 按原协议生成报告并返回非零状态；本次仍保留该行为，不把旧失败改成成功。本次做的是历史预测重算，没有重新请求大模型生成 200 个答案。另有成功录制夹具验证 CLI 正常返回 0、三个报告产物齐全，重复输出路径仍被拒绝。

## 现有 RAG 验证

再次调用当前本机部署的实际检索接口，两个原始问题均为 `evidence_found`；检查返回原文确实存在于关联文档正文，真实检索与模型重排均执行。

| 问题 | 证据数 | 证据 token | 关键条件与原文核对 |
|---|---:|---:|---|
| 成员迁移流程是怎样的 | 3 | 736 / 1200 | 通过 |
| 触发回滚的情况有哪些 | 1 | 202 / 1200 | 通过 |

此次修改属于离线评分与开发依赖；服务镜像原本没有安装 NLTK，不为本次变更重建运行中的检索模型镜像。

## 防回归与证据

CI 新增 dev/benchmark 依赖审计，同时将 CMRC 与英文分词测试纳入 evaluation job；不设置漏洞忽略项。测试依赖固定小型参考输出，不要求 CI 下载整个 CMRC 语料或 NLTK 模型。

- `evidence/nltk-parity-baseline.json`：卸载前基线；`check_nltk_parity.py` 复核两种分词、评分组合及完整历史报告。
- `evidence/nltk-parity-result.json`：卸载后对照结果。
- `evidence/nltk-repair-historical-score.json` 及同名 Markdown、预测文件：CLI 重算结果。
- `evidence/nltk-evaluation-tests.xml`、`evidence/nltk-tokenizer-final-tests.xml`：相关回归的分轮结果。
- `evidence/nltk-test-summary.json`：90 个测试节点去重后的最近成功验证来源。
- `evidence/nltk-repair-local-dependency-audit.json`：本机完整环境审计。
- `evidence/nltk-package-verification.json`：wheel 资源、隔离导入、参考源码来源核对。
- `evidence/nltk-repair-rag-verification.json`：原始两个问题的实际接口结果。
- `backend/_vendor/cmrc_tokenizer/manifest.json`：官方来源、固定提交及摘要。

原 `repair-local-dependency-audit.json` 保留为历史记录，仍反映修复前的 NLTK 告警，不应把它当作本次修复后的环境清单。
