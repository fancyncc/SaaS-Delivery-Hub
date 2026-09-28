# V3 blind holdout v1 results

## 中文摘要

这次测试使用 5 份全新编写且未参与开发调试的 SaaS 实施文档，在首次查询前冻结 30 道题、60 个必要证据片段及全部文件哈希。首次盲测的候选召回率为 100%，但最终证据覆盖率只有 86.7%；25 道可回答问题中 22 道完整命中，5 道无答案问题中只有 3 道被正确拒答。英文提问、中文资料这一跨语言场景的最终证据覆盖率仅为 61.9%。

因此，旧开发集上的 `318/318` 不能作为泛化能力证明。当前系统的主要问题不是初始召回，而是最终相关性筛选会丢弃已召回的跨语言正确证据，同时无答案判断会把语义相近但不包含答案的段落当作证据。本留出集已经暴露结果，不应再用于调参；后续改进应在独立开发集上完成，再使用新建且重新冻结的 `holdout_v2` 复测。

## Protocol

The corpus and labels were created after the original 192-case development corpus had already been used for pipeline iteration. Before the first retrieval query:

- `cases.jsonl` was frozen at SHA-256 `e953b32dd37350f670e9f453df5bc9b52392da4fbd4c35ce8324b0faaa7099ed`.
- All five source hashes were recorded in `sources.json`.
- All labelled spans were checked for literal presence in their source document.
- No retrieval code, model settings, labels or source content were changed after the first result was observed.

The first command attempt failed before database connection because the Windows Proactor event loop is incompatible with async psycopg. It produced no index or query result. The same frozen test was then run with a Selector event loop.

## Dataset

| Item | Count |
| --- | ---: |
| New SaaS implementation documents | 5 |
| Questions | 30 |
| Answerable questions | 25 |
| No-answer questions | 5 |
| Chinese questions | 20 |
| English questions over Chinese sources | 10 |
| Required source spans | 60 |

The cases cover direct facts, multi-span answers, enumerations, conditions, exceptions, contrasts, negative answers and cross-language retrieval. Labels remain agent-authored and are not independently reviewed.

## First-run result

Model profile: BGE-small-zh-v1.5 embeddings with BGE-reranker-base, parser/pipeline 5, rules relevance mode, 1200-token evidence budget.

| Metric | First blind run |
| --- | ---: |
| Candidate required-span recall | 60/60, **100%** |
| Final selected-span coverage | 52/60, **86.7%** |
| Fully covered answerable questions | 22/25, **88.0%** |
| Partially covered answerable questions | 0/25 |
| Zero-coverage answerable questions | 3/25, **12.0%** |
| No-answer questions correctly returning no evidence | 3/5, **60.0%** |
| No-answer false positives | 2/5, **40.0%** |
| Permission leaks | 0 observed |
| Citation integrity errors | 0 observed |
| Evidence budget violations | 0 observed |
| Warm query P95 | **5353.85 ms** |

### Language split

| Language | Required spans | Candidate recall | Final coverage | Zero-coverage answerable questions |
| --- | ---: | ---: | ---: | ---: |
| Chinese | 39 | 100% | **100%** | 0 |
| English over Chinese sources | 21 | 100% | **61.9%** | 3 |

## Recorded failures

### Relevant candidates rejected by final selection

- `holdout-05`: asked in English for the required digest format. The required Aurora span was present among candidates, but no evidence passed final relevance selection.
- `holdout-26`: asked in English for four P1 support targets. The exact Ember paragraph ranked as the first candidate, but final selection returned no evidence.
- `holdout-29`: asked in English for enhanced-support exit conditions. The exact Ember paragraph ranked as the first candidate, but final selection returned no evidence.

All three failures had complete candidate recall and zero final coverage. This isolates the main observed weakness to cross-language reranking/relevance filtering rather than initial retrieval.

### No-answer false positives

- `holdout-12`: a question asking for a hardware security-key brand returned a general emergency-access policy that contained no brand.
- `holdout-18`: a question asking for an object-storage region returned migration checksum and retention rules that contained no region.

These cases show that semantically adjacent passages can still be returned when the requested attribute is absent.

## Interpretation

The old `318/318` development-corpus result did not generalize to this first unseen corpus. Initial retrieval remained broad enough to recall every labelled span, but final evidence selection lost eight required spans and produced two unsupported no-answer matches.

This result must not be used to tune the frozen holdout. Improvements should be developed against separate documents and cases. A future measurement should use a newly authored `holdout_v2`, ideally with independent human labels, longer documents, multiple formats and real anonymized customer material.

The raw immutable first-run output is stored in `first_run.json`. This evaluation measures evidence retrieval, not generated-answer correctness.
