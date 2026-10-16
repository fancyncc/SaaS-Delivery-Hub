# 测试与评测操作

测试按功能回归、真实检索与生成评测、真实服务验证及性能测试区分。先运行不依赖外部服务的回归，再按环境条件运行浏览器、PostgreSQL、模型和隔离恢复验证。

## 文档导航

[agent.md](agent.md) · [api.md](api.md) · [architecture.md](architecture.md) · [data_pipeline.md](data_pipeline.md) · [deployment.md](deployment.md) · [evaluation_report.md](evaluation_report.md) · [rag.md](rag.md) · [testing.md](testing.md)

## 本文内容

- [CMRC 2018 准备、运行与评分](#cmrc-benchmark)
- [检索回归题库与发布协议](#retrieval-regression)
- [自建题库复核清单](#review-checklist)
- [历史留出集协议](#holdout-v1-protocol)

## 常用检查

以下命令均从项目根目录运行；全量后端测试与各套件共享测试数据库，应顺序执行。

```powershell
python -m pytest -q
python -m ruff check backend tests
python -m mypy backend
pnpm --dir frontend typecheck
pnpm --dir frontend build
```

完整运行浏览器回归时，先构建前端并安装 Playwright Chromium，然后设置
`RUN_BROWSER_TESTS=1`。需要隔离本机数据库时，用 `SAAS_TEST_DATABASE` 指定新的
SQLite 文件；不要让两个 pytest 进程使用同一个文件。新增的平台指代回归在
`tests/test_chat_planning.py`：明确的“这个平台/这个系统”问题直接检索，真正不清楚的
“它/那个平台”等问法仍可澄清。实际助手验证可执行
`python scripts/verify_platform_assistant.py --output data/acceptance/live-assistant.json`，
记录模式、引用类型和步骤；使用演示账号并删除本轮测试对话，不变更用户密码。
`tests/test_chat_citation_retry.py` 验证模型使用本轮 `evidence-编号` 引用 ID，
编号与 ID 仍需严格对应；保存和返回时保留真实来源及文档位置。实际验证可按
`--case runtime` 单独复核运行状态问答；失败原因记录在输出文件，不重写失败记录。

`evaluate_project.py isolated` 的隔离 Mailpit 禁用客户端反向 DNS，避免
Windows/Docker 网关 PTR 查询使连接探测超时；它不会连接外部收件人。
失败的逐项状态保存在
`raw/real-service-probes.json`，不输出凭证或任意异常文本。

可按行为采集独立报告：

```powershell
python scripts/evaluate_project.py suite --suite agent --output data/evaluations/agent-check
python scripts/evaluate_project.py suite --suite memory --output data/evaluations/memory-check
python scripts/evaluate_project.py suite --suite distributed_trace --output data/evaluations/trace-check
```

浏览器套件需要已构建前端、Playwright 和显式启用的测试环境；PostgreSQL/RLS、Worker 强制终止恢复和备份还原需要独立隔离环境。配置、指标门槛与必需项以 [evaluation.json](../config/evaluation.json) 为准。操作与实测结论分别放在本文和 [评测报告](evaluation_report.md)。

## 文档及数据维护

`docs/` 只维护本文导航中的八个主题文件。更新 API 时同步接口索引；更换文件位置时检查相对路径与锚点；历史报告保留测试日期、范围及版本。Skill 定义、夹具 Markdown、原文快照及题目预览仍保留在其数据或运行目录中。

旧业务题库的准备脚本读取 `evaluations/retrieval/business/sources/` 固定快照，不再从整理后的维护文档重建历史输入。它们是开发回归数据，不作为未见盲测集。


<a id="cmrc-benchmark"></a>

## CMRC 2018 准备、运行与评分

This evaluates the current application's retrieval and answer generation using
one public dataset. No custom questions, human label creation, or model judging
are required. It does not change `config/evaluation.json` or production acceptance.

The official public development split contains 3,219 questions in 848 paragraphs.
Data is pinned to upstream commit `c0eb1b6ba219847457e6af3180da722bbeb656af`,
with SHA-256 `86000878d2e8b535e33b36161a3032d74398881d8ee3ae16b2b22f1a48a99cc6`.
See [CMRC](https://github.com/ymcui/cmrc2018), its
[paper](https://aclanthology.org/D19-1600/), and the
[official v6 scorer](https://github.com/ymcui/cmrc2018/blob/c0eb1b6ba219847457e6af3180da722bbeb656af/baseline/cmrc2018_evaluate.py).

### Setup and run (PowerShell)

```powershell
.venv\Scripts\python.exe -m pip install -e '.[benchmark]'
.venv\Scripts\python.exe scripts/evaluate_cmrc.py prepare --output data/evaluations/cmrc2018-200 --limit 200 --seed 2018

# Start the configured API, PostgreSQL, OpenSearch, and retrieval model services.
# Reuse the existing Docker deployment and its real model configuration.
powershell -NoProfile -File scripts/run_local_cmrc.ps1 -OutputDirectory data/evaluations/cmrc-run
```

Preparation accepts `--source <official-dev.json>` for an offline download. A
different source must declare its real `--revision`; the pinned revision's bytes
are verified. `--limit 0` selects the full public development split.
Existing output paths are never overwritten. `-MaxCases 3` runs a smoke check
against the same prepared corpus; its report explicitly contains only three cases.

CMRC sentence and word tokenization use the bundled inference-only subset of
NLTK 3.10.3 and the same fixed English Punkt parameters as historical runs.
The NLTK package and downloader are no longer required. All four plain-text
parameter files are included in the installation and SHA-256 checked before use;
`NLTK_DATA` is ignored. Scoring works offline without a separate tokenizer setup.
Source, model revision and licenses are in `backend/_vendor/cmrc_tokenizer/`.
Reports retain `scoring.nltk_version` as `null` and identify the implementation,
reference version and parameter hashes under `scoring.tokenizer`; this does not
claim that the full NLTK distribution is installed or that it has been patched.

For direct execution in a configured Python environment:

```powershell
.venv\Scripts\python.exe scripts/evaluate_cmrc.py run --corpus data/evaluations/cmrc2018-200 --output data/evaluations/cmrc-run.json
.venv\Scripts\python.exe scripts/evaluate_cmrc.py score --corpus data/evaluations/cmrc2018-200 --runs data/evaluations/cmrc-run.json --output data/evaluations/cmrc-score.json
```

Real `MODEL_MODE`, `RAG_MODE`, PostgreSQL with the repository's current schema,
OpenSearch, and configured embedding/reranker endpoints are required. Mock runs
are rejected. The Docker helper copies the current workspace code into a separate
child-process directory. It leaves the serving API's source unchanged.

### Protocol

- Deterministic seed 2018 selects 200 questions without considering results.
  Their 182 unique original paragraphs form the shared retrieval corpus. Every
  query searches the entire prepared corpus; the gold paragraph is not supplied
  to retrieval. A sampled corpus is easier than full-corpus retrieval and is
  reported explicitly. Do not compare sampled and full-corpus scores directly.
- A temporary uncommitted tenant holds the documents. Indexing uses the actual
  application parser, embeddings, SQL vectors, and OpenSearch publisher.
  Retrieval uses `knowledge_retrieval.inspect`; generation uses `chat.answer_question`
  and the configured model adapter. No standard answer, offset, or reference
  paragraph is sent as a gold instruction to the model.
- The question asks for a short, complete answer and citations. Scoring removes
  only numeric citation markers. It does not extract a gold substring from a long
  answer or issue a second model request to improve the answer.
- EM and F1 use the official v6 normalization and best reference answer. CMRC F1
  uses longest **contiguous** token matching, not SQuAD bag-of-words overlap or
  longest common subsequence. Chinese characters and NLTK English tokens match
  the official implementation. Failures and missing predictions score zero.
- Answer-evidence hit@1/3/5 measures the reranked stage before relevance filtering.
  The selected-answer-position metric measures the final model evidence. A hit
  requires the correct document and a returned text interval enclosing an original
  answer offset; answer strings in another source are not credited. Whitespace
  introduced by source splitting is ignored for position alignment; ambiguous
  repeated intervals are not credited.
- Invalid official offsets are retained. A question remains eligible for evidence
  scoring if any of its original annotations has a valid position. Questions with
  no valid offset are excluded only from the evidence denominator; EM/F1 still
  use all official answer texts. The 200-question sample has 11 invalid individual
  annotations and no questions excluded from evidence scoring.
- Latency reports P50/P95 for retrieval, generation, and their full request;
  these are sequential warmed-service observations, not concurrent-load acceptance.
  Indexing and one retrieval warmup are recorded separately. Usage sums actual
  provider receipts, including retries. Missing usage is never estimated.
- To price usage, pass explicit `--input-per-million`, `--output-per-million`, and
  `--currency` to `score`. Without verified rates, cost remains `not_measured`.
- Manifest, source files, cases, code, model configuration, rows, and snapshots
  are bound by SHA-256. Raw observations are append-only and failed runs remain
  available. SQL rollback and generated OpenSearch-ID cleanup are verified.

Reports are `run.json`, its journal, `score.json`, `score.md`, and an ordinary
CMRC-compatible prediction dictionary. They describe a public development RAG
benchmark, not the original passage-given reading-comprehension leaderboard,
unseen-model generalization, semantic citation review, or production acceptance.
CMRC has no no-answer or tenant-permission annotations.
Collection or request errors produce a nonzero exit code, while the saved report
still includes their zero scores and error rate. Keep those observations; do not
rerun only failed questions to inflate the result.

### Attribution and license

Yiming Cui, Ting Liu, Wanxiang Che, Li Xiao, Zhipeng Chen, Wentao Ma, Shijin Wang,
and Guoping Hu. 2019. *A Span-Extraction Dataset for Chinese Machine Reading
Comprehension*. EMNLP-IJCNLP, pages 5886–5891.

CMRC data and the adapted scoring utilities are attributed under CC BY-SA 4.0.
The local dataset manifest retains source URL, exact revision, and license.
See [CMRC LICENSE](../evaluations/cmrc2018/LICENSE); raw downloaded data stays under ignored `data/`.

<a id="retrieval-regression"></a>

## 检索回归题库与发布协议

> 本节保留历史检索题库协议；其中历史结果、当前版本措辞属于原记录日期。当前已有 CMRC 实测，但它不完成 calibrated 发布或生产验收。命令入口已按当前文件名修正。

[The detailed Chinese evaluation report](evaluation_report.md#retrieval-report-20260928) consolidates the measured runs, question completeness, latency/token statistics, functional regressions and outstanding quality gates. On 2026-09-28 parser/pipeline 5 was deployed to local Docker, all 46 V3 indexes were rebuilt, and the real model service completed both source-backed and eight-format evaluations. [Supplementary statistics](../evaluations/retrieval/detailed_report_metrics.json) cover the historical runs; the current summaries are [pipeline 5 boundary observations](../evaluations/retrieval/local_context5_observations.json) and [source comparison](../evaluations/retrieval/business/comparison-v5.json).

Those pipeline 5 results are historical development observations, not the current code's quality result. A subsequently frozen [blind holdout](evaluation_report.md#holdout-v1-results) achieved only 86.7% final evidence coverage, 61.9% English-over-Chinese coverage and 60% correct no-answer rejection, so the earlier `318/318` result did not generalize. The current worktree uses pipeline schema 6 for bilingual answerability gating and has not yet been measured with the real retrieval models. Do not tune against `holdout_v1`; develop on separate cases and create a new frozen holdout after the implementation is fixed.

The calibrated V3 mode is not release-ready. The operational rules mode is documented in `docs/deployment.md#retrieval-operations` and does not claim calibrated quality. `cases.jsonl` contains 192 **synthetic boundary cases**, not independently reviewed labels. The calibration domains (inventory, education, facilities) and locked validation domains (publishing, events, software) have no overlap. Every domain contains all eight formats, including PDF, PPTX and XLSX. `python scripts/build_retrieval_binary_fixtures.py` regenerates the 18 binary fixtures and their 72 cases. Automated tests check that annotated facts and locations survive parsing; they do not establish retrieval quality. Human review, diverse long documents, comparison questions, scanned PDF cases and additional permission fixtures remain necessary. Do not change `review_status` merely because automated tests pass.

### Run

#### Source-pinned development questions

[The first source-backed question set](../evaluations/retrieval/business/questions.md) contains four maintained project-document snapshots and the public NIST IR 8397 PDF: 24 answerable questions and five no-answer questions. These are authentic project/public sources, not customer documents. Labels remain `agent_draft` and this is a development corpus; it does not replace locked validation or independent review. Snapshots and JSONL labels retain their measured bytes through Git attributes. `business/sources.json` records checksums, source URLs and the repository revision.

Run `pwsh -File scripts/record_local_retrieval_business.ps1 -OutputDirectory data/evaluations/business-run` against the current Docker deployment. `-UseWorkspaceBackend` evaluates a separate child-process code copy before deployment; the isolated parser imports that same copy. Complete reports require SQL rollback and verified cleanup of the generated search IDs. The evaluator checks document hashes before indexing, measures all five documents in the same tenant, and credits PDF facts only at the annotated physical page. Whitespace is normalized for matching while values, negation and original citation text remain intact. `scripts/summarize_retrieval_business_comparison.py` rejects incomplete/invalid runs, changed documents, labels, models or budgets. [Measured results](evaluation_report.md#business-results-20260928) distinguish development observations from release approval.

#### Operational observations on local Docker

`powershell -File scripts/record_local_retrieval_evaluation.ps1` records 40 authored XL-107 demo questions and the 192 synthetic boundary questions at budget 1200. Use `-Dataset Demo` or `-Dataset Boundary` for a subset; in a PowerShell session pass `-Budgets @(600,1200,1800)` to measure all budgets. Services must already be ready. Raw reports are saved under ignored `data/evaluations/<run-id>` and contain the selected evidence, candidate diagnostics, exact-source coverage, citation checks and warm latency. Demo SQL is read-only. Each synthetic format uses an uncommitted tenant, rolls back its SQL and deletes only its generated OpenSearch source IDs, then verifies cleanup. Parsing/indexing time is separate from warm query time. Run evaluations sequentially on the same model service to keep latency measurements comparable.

These observations do not train a classifier, claim independent review, measure an older baseline, or authorize calibrated mode. Short fact values use token boundaries and annotated source keys; protected conditions and exact source metadata/text must survive. This deterministic scoring still needs reviewer confirmation, especially for negative answers and semantic completeness.

The local measurements from 2026-09-27 are recorded in `local_demo_observations.json` (120 runs at three budgets) and `local_boundary_observations.json` (192 runs, eight formats). At budget 1200 the demo's 54 necessary source spans survived, but the synthetic corpus's final selected span coverage was only 21.1%, despite candidate recall of 99.4%. All eight formats were below the 90% selected-coverage target. Citation, scope and budget checks found zero violations in these runs, not a universal permission guarantee. The current heuristic threshold/model pair must not be represented as quality-approved. One demo no-answer annotation returned an explicit source statement that real keys are not provided; it needs reviewer assessment. See [the independent review checklist](testing.md#review-checklist).

These earlier observations used parser/pipeline 3. After the source-context and PDF changes, [parser/pipeline 4 observations](../evaluations/retrieval/local_context_observations.json) cover the same 192 labels: selected coverage improves to 62.3%, with zero observed no-answer, citation, scope or budget violations. Only TXT reaches 90%; quality gates remain unmet. The code was frozen before this full run, and validation-domain results were not used for another code adjustment. Source-backed development observations are reported separately in [business/RESULTS.md](evaluation_report.md#business-results-20260928); PDF there still achieves only 62.5% selected coverage.

[Parser/pipeline 5 observations](../evaluations/retrieval/local_context5_observations.json) cover the same 192 labels after deployment and reindexing. Candidate recall and selected coverage are 318/318 (100%); every format, including PDF and XLSX, reaches 100%. All 48 no-answer cases return no evidence, with zero observed citation, scope or budget violations. The 29 source-backed questions also reach 44/44 selected spans, including PDF 8/8. These results pass local automated technical gates, while independent review, representative customer documents, paired pipeline 5 model comparison and generated-answer scoring remain required for formal quality approval.

The optional `docker-compose.evaluation.yml` adds an isolated model service, with no host port, a 5 GiB memory cap, read-only model cache and pinned settings in `config/retrieval.v3-multilingual.env.example`. It is not enabled by the normal local deployment. Download the candidate weights explicitly with `model-download` and matching environment overrides before starting it. The download script verifies the weight commit, prefers safetensors and supports the pinned BGE-M3 PyTorch BIN weights; missing weights stop setup. The candidate revisions were checked against the official [BGE-M3 revision](https://huggingface.co/BAAI/bge-m3/tree/5617a9f61b028005a4858fdac845db406aefb181) and [reranker revision](https://huggingface.co/BAAI/bge-reranker-v2-m3/tree/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e).

Start the candidate with `docker compose -f docker-compose.yml -f docker-compose.evaluation.yml --profile v3-evaluation up -d --no-deps retrieval-evaluation`, wait for `/health/ready`, then run `./scripts/record_local_retrieval_evaluation.ps1 -Dataset Boundary -ModelProfile config/rag.v3.multilingual.example.json`. Only the evaluation subprocess receives the profile; deployed API/chat/Agent settings remain their configured values. On limited-memory hosts, evaluate sequentially and restore the operational retrieval service before business checks. Operational observations still do not replace calibrated paired baseline/locked validation.

`./scripts/compare_local_v3_models.ps1` automates sequential measurements, candidate readiness and restoration of the configured model service in a `finally` block, including when candidate warmup/evaluation fails. Reports go to `data/evaluations/paired-<run-id>`; no model is automatically selected. `-CurrentReport <raw-boundary-report>` reuses complete observations on the exact corpus and `-SkipDownload` uses already prepared weights. Both models must be measured on the same host; formal acceptance also requires independently reviewed labels and paired older baselines. The candidate may fail its resource cap; record that result without increasing the local engine's memory or changing the active model silently.

The candidate's actual 192 observations are summarized in `local_model_comparison.json`: selected span coverage was 30.8%, with only JSON reaching 90%; warm P95 was 14,007 ms versus the current model's 2,931 ms. Neither profile passed overall quality acceptance. The operational model was restored and the optional candidate container removed; weights remain cached. See [local acceptance and remaining work](evaluation_report.md#local-acceptance-20260928).

#### Formal calibrated acceptance

1. Apply migrations through the repository head; set `RAG_V3_INDEXING_ENABLED=true` in a staging environment. Upload files via `/api/knowledge/files`, or enqueue existing text via `POST /api/knowledge/{id}/v3-index`. PDF/PPTX/XLSX require their saved originals. Keep the operational relevance mode at `rules` until calibrated validation is approved.
2. Compare the current BGE pair against `BAAI/bge-m3` / `BAAI/bge-reranker-v2-m3` in a **separate model service**. The multilingual model is a candidate, not a selected winner. Set `RAG_V3_MODEL_PROFILE` to a JSON settings file; it overrides only V3 embedding/reranker settings. Pin model revisions to 40-character weight commits. Do not use `main` in a release. V3 vectors support 512 and 1024 dimensions and authorize/filter identity before distance evaluation.
3. Record candidate features and independently reviewed evidence labels as JSONL: `split`, `relevant`, `evidence_label`, `language_pair`, and the exact feature mapping declared by `backend.rag_v3_release.FEATURES`. Include `SUPPORT`, `PARTIAL`, `CONTRADICT`, and `TOPIC_ONLY` examples. Run `python scripts/evaluate_retrieval.py calibrate --runs calibration-features.jsonl --output classifier.json`. Only calibration rows are accepted. Freeze classifier and manifest hashes before validation.
4. Record paired runs with fields `id`, `format`, `kind`, `necessary_total`, `recalled_necessary`, `covered_necessary`, `old_covered_necessary`, `old_tokens`, `final_tokens`, `budget`, `returned_relevant`, `permission_leaks`, `source_mismatches`, `fact_corruptions`, `warm_latency_ms`, `old_warm_latency_ms`. For PDF/PPTX/XLSX, use the same content in an authorized MD/TXT reference as the `old_*` comparator; do not describe this as an older binary parser. Both runs must use the same machine, model profile, question and permission scope. Necessary-fact matching requires verified source positions, values and protected conditions, not substring-only keyword checks. Cold starts, parsing and indexing durations are recorded separately.
5. Run `python scripts/evaluate_retrieval.py validate --cases evaluations/v3/cases.jsonl --runs locked-runs.jsonl --output report.json`. Failed gates remain failures; validation must not be used to tune parameters. Pair the model profiles by Chinese/English macro recall, completeness, and warm latency. Equivalent results favor the current pair.
6. Independently review the report. The release report must contain checked metrics for all eight formats, model comparison and reviewer approval, `identity` and SHA256 of canonical classifier JSON. A release file contains `identity`, `classifier`, `report`, and `report_sha256`. Only then set `RAG_V3_RELEVANCE_MODE=calibrated`; the API checks the release before retrieval. Keep chat and Agent on the operational V3 rules mode until this approval.

### Current limitations / release blockers

- Synthetic corpus is not independently reviewed and is not sufficiently diverse for release.
- Two real model services have been measured on local synthetic observations. Independently reviewed calibration, paired older baselines, locked validation and formal latency acceptance are pending.
- Long indivisible atoms are split only for indexing; they are reassembled only when all required parts pass relevance filtering and fit the budget. Otherwise they are omitted with an explicit diagnostic.
- Exact body duplicates are deduplicated within a source; cross-source near-duplicate merging is deliberately conservative. JSON path conflicts are flagged as potential conflicts, not resolved answers.
- Query evidence uses the retrieval tokenizer; future generation tokenizer and full request budgets are outside this release.

Model references: [BGE-M3](https://huggingface.co/BAAI/bge-m3), [BGE reranker v2 M3](https://huggingface.co/BAAI/bge-reranker-v2-m3). Review the exact pinned revisions used by the model service, not only these mutable landing pages.

Rollback of the calibrated classifier: set `RAG_V3_RELEVANCE_MODE=rules`. Pause indexing separately with `RAG_V3_INDEXING_ENABLED=false`. Preserve failed reports and side-index data for diagnosis. Knowledge PDF, PPTX and XLSX remain on V3; unsupported DOC/PPT/XLS require conversion.

<a id="review-checklist"></a>

## 自建题库复核清单

> 此清单用于自建质量题库及 calibrated 发布。只做 CMRC 公开数据集指标检测时，无需人工重做题库；不能因此签发完整生产验收。

当前 `cases.jsonl` 的 192 题均为合成样本，`inspection_cases.py` 的 40 题来自演示项目。自动观测报告不代表独立复核通过；正式验收还须补充脱敏真实资料、长文档、扫描件、比较问题与权限边界样本。

### 逐题复核

- 对照原文件确认问题确实可回答或不可回答，不能仅根据检索是否返回片段决定标准答案。
- 确认必要事实、完整枚举、数值、单位、否定、条件和例外；标记无关值及容易混淆的段落。
- 标注文档版本及来源位置：PDF 页码、PPTX 幻灯片、XLSX 工作表与行列、文本段落、JSON 路径。
- 检查最终 `chunks` 和 `evidence_text`，候选诊断不能代替最终证据覆盖率。
- 对照原文确认引用正文、来源和位置准确；核对租户、项目、停用及最新版本权限。
- 评测报告中的完整语句、词边界和来源键匹配是辅助计数。语义等价、跨段组合、明确否定答案及完整性仍需人工确认。

每题记录 `case_id`、`source_sha256`、必要事实及位置、复核决定（接受／修改／拒绝）、原因、复核人和时间。修改题目或答案后重新冻结语料哈希；校准与验证按领域及资料组隔离。

### 报告复核

- 记录机器、模型权重提交、tokenizer、流程版本、语料与分类器哈希。
- 校准只使用 calibration；冻结后再运行 validation，不能按验证结果调参。
- 同一机器、问题、范围、模型配置及预算下记录基线和新流程，分别记录冷启动、解析、索引及热查询耗时。
- PDF／PPTX／XLSX 的旧基线使用内容等价的授权 MD／TXT，禁止编造旧二进制解析结果。
- 逐格式核对召回≥95%、覆盖≥90%且不低于基线、无答案误报≤5%、事实题中位 token 减少≥25%、热查询 P95 比值≤1.5，以及零权限／来源／事实／预算违规。
- 完成两个固定模型组合的配对评测。留存失败项；通过指标、独立复核和模型对比后才批准 calibrated 发布。

当前观测命令与原始报告位置见 [评测说明](testing.md#retrieval-regression)。凭据、真实业务原文及含个人数据的原始观测只保存在本机受控目录。

<a id="holdout-v1-protocol"></a>

## 历史留出集协议

> 留出集首次运行后已暴露，仅保留协议与来源记录供回归分析。

This is a one-time generalization check created after the original 192-case development corpus had already been used for pipeline iteration.

- Five newly authored SaaS implementation documents.
- Thirty questions: 25 answerable and five no-answer cases.
- Unseen organizations, identifiers, numbers, wording and document structure.
- Chinese and English questions covering direct facts, enumerations, conditions, exceptions, contrasts and multi-span answers.
- Labels are agent-authored and have not received independent human review.
- The corpus is frozen by SHA-256 before the first run.
- No labels, retrieval rules, model settings or source documents may be changed after viewing the first result. Failures remain recorded.

This dataset measures retrieval evidence coverage only. It does not measure generated-answer correctness and is not a formal production acceptance set.

Run with:

```powershell
.venv\Scripts\python.exe scripts\record_v3_business_runs.py `
  --cases evaluations\v3\holdout_v1\cases.jsonl `
  --output evaluations\v3\holdout_v1\first_run.json `
  --budget 1200
```
# 安全回归

## SQLite 迁移与唯一索引

`tests/test_sqlite_migration_integrity.py` 在独立子进程中将 SQLAlchemy `SAWarning` 升级为错误，验证空库升级到 head、带数据回退至 0027、再升级到 head。CI 后端测试同样将 `SAWarning` 作为失败处理。

`test_project_collaboration_migration.py` 和 `test_task_review_migration.py` 验证真实旧表形态的升级、回退和再次升级，逐阶段比较 users 索引原始 SQL，直接执行 INSERT/UPDATE 检查大小写和首尾空格重复值被数据库拒绝，检查原文、任务、状态约束与外键。最终启用 SQLite 外键，确认无效引用实际被拒绝。

子表 batch 反射设置 `resolve_fks=False`，避免递归反射未修改的 users 表；外键定义仍由 Alembic 保留。0015 必须重建 users，因此在 SQLite SAVEPOINT 内保存表达式索引原始 DDL、重建表并恢复索引，包含自定义表达式和部分索引。`test_profile_migration.py` 覆盖空表、有数据、可空联系方式，以及重建表或恢复索引阶段失败时的原子回滚。没有添加忽略告警的过滤器。

```powershell
.venv\Scripts\python.exe -m pytest tests/test_profile_migration.py tests/test_project_collaboration_migration.py tests/test_task_review_migration.py tests/test_sqlite_migration_integrity.py -q -W error -o cache_dir=tmp/pytest-migration
```

0015 原有的禁止破坏性回退规则继续保留；上述回退验证不会尝试删除仅用户名注册的登录身份。

## 账号与 PostgreSQL 安全检查

`tests/test_security_remediation.py` 检查公开账号接口不泄露凭证、强制改密、重置令牌失效、检索配额、并发租约释放、审批原文快照和启用外键后的清理。`tests/test_security_migration.py` 检查空库升级和 0034 回退再升级。

为独立的本机测试数据库设置 `SECURITY_TEST_POSTGRES_URL` 后执行 `python tests/check_security_postgres.py`。库名必须以 `security_remediation_test` 开头；脚本不会清空已有业务库。它使用非所有者、`NOBYPASSRLS` 账号验证共享项目检索、公司私有资料隔离、撤销协作后失权，以及真实 PostgreSQL 下上传/审批和双重密码重置的并发行为。CI 为这些检查启动独立 PostgreSQL 服务，并执行 mypy、前端审计和运行时 Python 依赖审计。

CMRC 已移除 NLTK 整包依赖，仅保留英文 Punkt/Treebank 分词所需的推理代码与固定参数，保留原分词和 EM/F1 规则。`PYSEC-2026-3740` 实际涉及模型文件接口绕过路径沙箱；原记录称其为下载器告警不准确。相关模型文件读写、训练、pickle、下载及外部数据目录发现接口均未纳入保留代码。已有环境需先更新项目再执行 `python -m pip uninstall -y nltk`；pip 更新依赖不会自动卸载旧包。CI 同时审计服务依赖与 dev/benchmark 依赖，不忽略该告警；分词参数损坏时评分直接失败，不能降级为其他分词算法。
