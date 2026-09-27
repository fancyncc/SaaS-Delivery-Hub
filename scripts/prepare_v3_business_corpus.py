"""Freeze maintained project documents and a checksum-pinned public NIST PDF.

Labels are agent drafts, not independent business review or customer documents.
No network requests or business database writes are made by this preparer.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PDF_SHA = "cadab12c1f7b1a6a8f844d19180381211e1c29d05554bd9efb277442d8fd983d"
PDF_URL = "https://nvlpubs.nist.gov/nistpubs/ir/2021/NIST.IR.8397.pdf"

# Exact source spans: they describe the frozen documents, not current deployment.
QUESTIONS = [
    ("RAG_BINARY_DOCUMENTS.md", "知识库 PDF 文件大小、页数和 OCR 页数上限分别是多少？", ["10 MiB、100 页", "最多 30 页"], None),
    ("CONTEXT_MEMORY.md", "记忆候选是否只打开部署开关就会自动提取？", ["用户还必须通过", "显式启用", "auto_extract"], None),
    ("CHAT_AGENT.md", "聊天流式回答中途失败后会保存半截答案吗？", ["不保存半截答案", "重试相同 request_id 返回已保存的"], None),
    ("PREPRODUCTION_RUNBOOK.md", "PostgreSQL 备份是否包括 S3 交付物？应该另外备份什么？", ["PostgreSQL dump 不包含 S3 对象", "需另外备份 artifacts 卷"], None),
    ("NIST.IR.8397.pdf", "According to NIST IR 8397, what minimum code coverage should structural test cases reach?", ["at least 80 % coverage"], 12),
    ("RAG_BINARY_DOCUMENTS.md", "XLSX 知识文档会计算公式或打开外部链接吗？", ["已保存的单元格值", "不计算公式，不打开外部链接"], None),
    ("RAG_BINARY_DOCUMENTS.md", "PPTX 解析能识别图片内容吗？", ["文字框和表格；图片内容不识别"], None),
    ("RAG_BINARY_DOCUMENTS.md", "知识库上传的解析结果最多允许多少字符和节点？", ["最多 15 万字符或 1 万结构节点"], None),
    ("RAG_BINARY_DOCUMENTS.md", "知识库 PDF 和 Office 隔离解析的最长时间分别是多少？", ["PDF 最长 10 分钟", "Office 最长 2 分钟"], None),
    ("RAG_BINARY_DOCUMENTS.md", "项目知识文档失败后需要什么权限才能重试？", ["project.document.submit"], None),
    ("RAG_BINARY_DOCUMENTS.md", "聊天 PDF 附件的支持范围和大小上限是什么？", ["仅支持带文字层的 PDF 附件", "2 MiB 上限未改变"], None),
    ("CONTEXT_MEMORY.md", "记忆候选有效期多久，已拒绝的候选会重复提出吗？", ["候选有效期 30 天", "已拒绝的同一候选不重复提出"], None),
    ("CONTEXT_MEMORY.md", "相同 key 的私有记忆在哪个作用域优先？", ["conversation > project > workspace > user"], None),
    ("CONTEXT_MEMORY.md", "维护摘要的模型会读取助手回答、文档或工具结果吗？", ["不读取助手回答、文档或工具结果"], None),
    ("CONTEXT_MEMORY.md", "保存记忆候选并替换冲突条目需要哪些版本参数？", ["expected_version", "replace_id", "replace_version"], None),
    ("CHAT_AGENT.md", "聊天工具检索最多进行多少轮，每轮允许几个工具？", ["最多两轮、每轮一个只读检索工具"], None),
    ("CHAT_AGENT.md", "聊天 SSE 包括哪些事件？", ["step", "delta", "done", "error"], None),
    ("CHAT_AGENT.md", "MCP 接口是否支持匿名访问或第三方 OAuth？", ["不是公开匿名 MCP 服务", "尚未提供第三方 OAuth 接入"], None),
    ("PREPRODUCTION_RUNBOOK.md", "预生产导入 CSV 的人数、管理员和部门要求是什么？", ["80 人 CSV", "包含一名 admin", "部门须与目标配置一致"], None),
    ("PREPRODUCTION_RUNBOOK.md", "执行人排队后被撤销权限，worker 能继续实施吗？", ["禁止 Worker 提升权限继续执行"], None),
    ("NIST.IR.8397.pdf", "What should automated verification ensure about static analysis and test results?", ["ensure that static analysis does not report new weaknesses", "check results accurately"], 13),
    ("NIST.IR.8397.pdf", "NIST IR 8397 推荐检查代码中的哪些硬编码秘密？", ["hardcoded passwords", "private encryption keys"], 14),
    ("NIST.IR.8397.pdf", "According to NIST IR 8397, when should threat modeling be repeated during development?", ["multiple times during development", "especially when developing new capabilities"], 12),
    ("NIST.IR.8397.pdf", "NIST IR 8397 说黑盒测试应基于什么？", ["functional speciﬁcations or requirements"], 14),
]
NO_ANSWERS = [
    ("RAG_BINARY_DOCUMENTS.md", "知识库是否支持 DWG 文件，并可保证 CAD 图纸识别准确率为 99.99%？"),
    ("CONTEXT_MEMORY.md", "该系统对所有用户提供无限期免费云端记忆存储的合同条款是什么？"),
    ("CHAT_AGENT.md", "这个产品已通过哪家机构的 ISO 27001 认证，证书编号是什么？"),
    ("PREPRODUCTION_RUNBOOK.md", "正式生产环境保证的每月 SLA 赔偿比例是多少？"),
    ("NIST.IR.8397.pdf", "What is NIST IR 8397's mandatory subscription price for the SaaS_Agent product?"),
]


def prepare(pdf, output):
    raw = pdf.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PDF_SHA:
        raise ValueError("NIST PDF differs from the reviewed source checksum")
    output.mkdir(parents=True, exist_ok=True)
    sources = output / "sources"
    sources.mkdir(exist_ok=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    registry = []
    for name in dict.fromkeys(q[0] for q in QUESTIONS):
        path = pdf if name.endswith(".pdf") else ROOT / "docs" / name
        shutil.copyfile(path, sources / name)
        registry.append({
            "document": "sources/" + name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source_kind": "public_government_publication" if name.endswith(".pdf") else "maintained_project_document",
            "source": PDF_URL if name.endswith(".pdf") else "docs/" + name,
            "revision": None if name.endswith(".pdf") else revision,
            "license": "US government work" if name.endswith(".pdf") else "repository license",
        })
    cases = []
    for i, (name, question, facts, page) in enumerate(QUESTIONS, 1):
        cases.append({
            "id": f"business-{i:02}", "document": "sources/" + name,
            "format": Path(name).suffix[1:], "question": question,
            "necessary_facts": facts, "protected_conditions": [], "source_keys": [],
            "required_page": page, "kind": "answerable", "split": "development",
            "language": "en" if question[0].isascii() else "zh",
            "review_status": "agent_draft",
        })
    for i, (name, question) in enumerate(NO_ANSWERS, len(cases) + 1):
        cases.append({
            "id": f"business-{i:02}", "document": "sources/" + name,
            "format": Path(name).suffix[1:], "question": question,
            "necessary_facts": [], "protected_conditions": [], "source_keys": [],
            "required_page": None, "kind": "no_answer", "split": "development",
            "language": "en" if question[0].isascii() else "zh",
            "review_status": "agent_draft",
        })
    (output / "cases.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    (output / "sources.json").write_text(json.dumps({
        "independent_review_completed": False, "customer_documents": False,
        "scope": "project operation and public software verification; development corpus",
        "sources": registry,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 首轮 RAG 质量题库", "", "共 5 份真实来源、29 道题（24 道有答案、5 道无答案）。前五题是优先检查的问题。", "",
             "资料包括仓库维护文档的固定快照和 NIST 公开 PDF，不是客户业务资料。答案标签由代理起草，尚未独立复核；本题库用于开发诊断，不替代锁定验证集。", "",
             "测试候选召回、最终证据、引用位置、预算和无答案误报；证据覆盖不等于生成回答正确率。", ""]
    for c in cases:
        lines.extend([f"## {c['id']}：{c['question']}", "", f"来源：[原文]({c['document']})" + (f"，PDF 第 {c['required_page']} 页（文件页序）" if c["required_page"] else ""), "",
                      "核对片段：" + ("；".join(f"`{f}`" for f in c["necessary_facts"]) or "来源中没有支持该结论的证据，应返回证据不足。"), ""])
    lines.extend(["## 重现", "", "运行 `scripts/record_v3_business_runs.py --cases evaluations/v3/business/cases.jsonl --output <raw-report.json>`。", "",
                  "在本机 Docker 中使用 `scripts/record_local_v3_business.ps1`；资料在未提交的临时租户中索引，完成或异常后回滚 SQL 并清理相应搜索条目。", "",
                  "人工复核应逐题检查原文、必要条件、无答案标签和错误引用；修改标签后生成新版本并重新记录基线。", ""])
    (output / "questions.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "evaluations/v3/business")
    args = parser.parse_args()
    prepare(args.pdf, args.output)
