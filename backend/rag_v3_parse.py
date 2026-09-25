"""Format-aware, non-generative parsing. Original slices and locations are retained."""

import csv
import io
import json
import math
import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from fastapi import HTTPException

from backend.rag_v3_binary import parse_pdf, parse_pptx, parse_xlsx, validate_binary

MAX_BYTES = 2 * 1024 * 1024
PDF_MAX_BYTES = 10 * 1024 * 1024
OFFICE_MAX_BYTES = 5 * 1024 * 1024
MAX_TEXT = 150000
SUPPORTED = {".docx", ".md", ".txt", ".csv", ".json", ".pdf", ".pptx", ".xlsx"}


def max_bytes(suffix: str) -> int:
    return PDF_MAX_BYTES if suffix == ".pdf" else OFFICE_MAX_BYTES if suffix in {".pptx", ".xlsx"} else MAX_BYTES


@dataclass
class Node:
    id: str
    kind: str
    text: str
    location: dict
    parent: str | None = None
    heading: str = ""
    record: str | None = None
    context: str = ""


@dataclass
class Parsed:
    format: str
    nodes: list[Node] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ocr_pages: int = 0

    def dump(self):
        return asdict(self)


def validate_filename(filename):
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise HTTPException(415, "支持 PDF、PPTX、XLSX、DOCX、Markdown、TXT、CSV、JSON；旧版 DOC、PPT、XLS 请转换格式")
    return suffix


def parse(filename: str, raw: bytes, *, csv_header: bool = True) -> Parsed:
    suffix = validate_filename(filename)
    if len(raw) > max_bytes(suffix):
        raise HTTPException(413, f"文件不能超过 {max_bytes(suffix) // (1024 * 1024)} MiB")
    validate_binary(suffix, raw)
    result = Parsed(suffix[1:])
    text_size = 0

    def add(kind, text, location, **kw):
        nonlocal text_size
        n = Node(str(len(result.nodes)), kind, text, location, **kw)
        result.nodes.append(n)
        text_size += len(n.text) + len(n.context)
        if len(result.nodes) > 10000 or text_size > MAX_TEXT:
            raise HTTPException(413, "提取内容超过 15 万字符或结构节点过多")
        return n.id

    try:
        if suffix == ".pdf":
            parse_pdf(raw, add, result)
        elif suffix == ".pptx":
            parse_pptx(raw, add, result)
        elif suffix == ".xlsx":
            parse_xlsx(raw, add, result)
        elif suffix == ".docx":
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                if any(
                    re.match(r"word/(?:header|footer|footnotes|endnotes)", name)
                    for name in z.namelist()
                ):
                    result.warnings.append("页眉、页脚、脚注及尾注未索引；请将必要条件写入正文")
                xml = (
                    z.read("word/document.xml")
                    if z.getinfo("word/document.xml").file_size <= 4 * 1024 * 1024
                    else b""
                )
                if not xml or b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
                    raise ValueError("invalid document XML")
                root = ET.fromstring(xml)
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

            def paragraph_text(element):
                return "".join(
                    (t.text or "")
                    if t.tag == ns + "t"
                    else "\t"
                    if t.tag == ns + "tab"
                    else "\n"
                    if t.tag in (ns + "br", ns + "cr")
                    else ""
                    for t in element.iter()
                )

            body = root.find(ns + "body")
            if body is None:
                raise ValueError("missing body")
            headings = []
            if any(
                e.tag.endswith(("}drawing", "}pict", "}object", "}oMath", "}oMathPara"))
                for e in root.iter()
            ):
                result.warnings.append("图片、嵌入对象及公式未解析；仅索引可提取文字")
            list_start = None
            previous_num = None
            for i, e in enumerate(body):
                num = e.find(f"{ns}pPr/{ns}numPr/{ns}numId") if e.tag == ns + "p" else None
                num_id = num.get(ns + "val") if num is not None else None
                if num_id is None or num_id != previous_num:
                    list_start = i
                previous_num = num_id
                if e.tag == ns + "p":
                    text = paragraph_text(e)
                    style = e.find(f"{ns}pPr/{ns}pStyle")
                    style_name = style.get(ns + "val", "") if style is not None else ""
                    level = re.search(r"(?:Heading|标题)\s*(\d+)", style_name, re.I)
                    if level:
                        depth = int(level[1])
                        headings = [(d, k, t) for d, k, t in headings if d < depth]
                        identifier = add(
                            "heading",
                            text,
                            {"block": i},
                            parent=headings[-1][1] if headings else None,
                            heading=text,
                        )
                        headings.append((depth, identifier, text))
                    elif text.strip():
                        kind = (
                            "list_item" if e.find(f"{ns}pPr/{ns}numPr") is not None else "paragraph"
                        )
                        if kind == "list_item":
                            result.warnings.append("列表保留条目及顺序；自动编号样式未展开")
                        location = {"block": i}
                        if kind == "list_item" and num_id is not None:
                            location["list_scope"] = f"docx:{list_start}:{num_id}"
                        add(
                            kind,
                            text,
                            location,
                            parent=headings[-1][1] if headings else None,
                            heading=" / ".join(t for _, _, t in headings),
                        )
                elif e.tag == ns + "tbl":
                    rows = e.findall(ns + "tr")
                    cells = [
                        [
                            "\n".join(paragraph_text(p) for p in c.findall(ns + "p"))
                            for c in row.findall(ns + "tc")
                        ]
                        for row in rows
                    ]
                    if e.find(".//" + ns + "tbl") is not None:
                        result.warnings.append(f"表格块 {i} 含嵌套表格，内层内容未解析")
                    explicit_header = bool(
                        rows and rows[0].find(f"{ns}trPr/{ns}tblHeader") is not None
                    )
                    headers = cells[0] if explicit_header else []
                    if not explicit_header:
                        result.warnings.append(f"表格块 {i} 未声明表头，使用列序号")
                    if (
                        e.find(".//" + ns + "vMerge") is not None
                        or e.find(".//" + ns + "gridSpan") is not None
                    ):
                        result.warnings.append(f"表格块 {i} 含合并单元格，列关系需人工核对")
                    for rownum, row in enumerate(cells):
                        for col, value in enumerate(row):
                            if explicit_header and rownum == 0:
                                continue
                            add(
                                "table_cell",
                                value,
                                {"block": i, "row": rownum + 1, "column": col + 1},
                                record=f"table:{i}:row:{rownum}",
                                context=headers[col] if col < len(headers) else f"列 {col + 1}",
                                parent=headings[-1][1] if headings else None,
                                heading=" / ".join(t for _, _, t in headings),
                            )
        else:
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise HTTPException(422, "文本编码无效，请使用 UTF-8") from None
            if len(text) > MAX_TEXT:
                raise HTTPException(413, "文本超过 15 万字符")
            if suffix == ".csv":
                reader = csv.reader(io.StringIO(text), strict=True)
                rows = list(reader)
                headers = rows[0] if csv_header and rows else []
                width = len(headers) if headers else max(map(len, rows), default=0)
                for rownum, row in enumerate(rows):
                    if csv_header and rownum == 0:
                        continue
                    if len(row) != width:
                        result.warnings.append(f"第 {rownum + 1} 条记录列数与表头不同")
                    for col, value in enumerate(row):
                        add(
                            "table_cell",
                            value,
                            {"record": rownum + 1, "column": col + 1},
                            record=f"row:{rownum + 1}",
                            context=headers[col] if col < len(headers) else f"列 {col + 1}",
                        )
            elif suffix == ".json":

                def pairs(items):
                    d = {}
                    for k, v in items:
                        if k in d:
                            raise ValueError("duplicate JSON key")
                        d[k] = v
                    return d

                data = json.loads(
                    text,
                    object_pairs_hook=pairs,
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError("non-finite JSON")
                    ),
                )

                def walk(value, path="$", parent=None, depth=0):
                    if depth > 64:
                        raise ValueError("JSON too deep")
                    if isinstance(value, float) and not math.isfinite(value):
                        raise ValueError("JSON numeric overflow")
                    if isinstance(value, (dict, list)):
                        identifier = add(
                            "object" if isinstance(value, dict) else "array",
                            "",
                            {"path": path},
                            parent=parent,
                            record=path,
                        )
                        for k, v in value.items() if isinstance(value, dict) else enumerate(value):
                            child = path + "[" + json.dumps(k, ensure_ascii=False) + "]"
                            walk(v, child, identifier, depth + 1)
                    else:
                        add(
                            "json_value",
                            json.dumps(value, ensure_ascii=False),
                            {"path": path},
                            parent=parent,
                            record=parent,
                            context=path,
                        )

                walk(data)
            elif suffix == ".md":
                from markdown_it import MarkdownIt

                tokens = MarkdownIt("commonmark").enable("table").parse(text)
                lines = text.splitlines(keepends=True)
                headings = []
                occupied = set()
                list_scopes = []
                for i, t in enumerate(tokens):
                    if t.type in {"bullet_list_open", "ordered_list_open"}:
                        list_scopes.append(f"md:{t.map}")
                    elif t.type in {"bullet_list_close", "ordered_list_close"}:
                        list_scopes.pop()
                    if t.type == "heading_open":
                        title = tokens[i + 1].content
                        depth = int(t.tag[1:])
                        headings = [h for h in headings if h[0] < depth]
                        identifier = add(
                            "heading",
                            title,
                            {"lines": t.map},
                            parent=headings[-1][1] if headings else None,
                            heading=title,
                        )
                        headings.append((depth, identifier, title))
                        if t.map:
                            occupied.update(range(*t.map))
                    elif (
                        t.type
                        in {"paragraph_open", "fence", "code_block", "table_open", "html_block"}
                        and t.map
                    ):
                        start, end = t.map
                        if any(n in occupied for n in range(start, end)):
                            continue
                        occupied.update(range(start, end))
                        if t.type == "table_open":
                            headers = []
                            rownum = 0
                            col = 0
                            in_header = False
                            for part in tokens[i + 1 :]:
                                if part.type == "table_close":
                                    break
                                if part.type == "thead_open":
                                    in_header = True
                                if part.type == "thead_close":
                                    in_header = False
                                if part.type == "tr_open":
                                    rownum += 1
                                    col = 0
                                if part.type == "inline":
                                    col += 1
                                    if in_header:
                                        headers.append(part.content)
                                    else:
                                        add(
                                            "table_cell",
                                            part.content,
                                            {
                                                "table_lines": [start + 1, end],
                                                "record": rownum,
                                                "column": col,
                                            },
                                            record=f"table:{start}:row:{rownum}",
                                            context=headers[col - 1]
                                            if col <= len(headers)
                                            else f"列 {col}",
                                            parent=headings[-1][1] if headings else None,
                                            heading=" / ".join(h[2] for h in headings),
                                        )
                            continue
                        content = "".join(lines[start:end]).rstrip("\n")
                        kind = {
                            "fence": "code",
                            "code_block": "code",
                            "table_open": "table",
                            "html_block": "unparsed_html",
                        }.get(t.type, "paragraph")
                        if t.type == "paragraph_open" and t.level > 0:
                            kind = "list_item"
                        if kind == "unparsed_html":
                            result.warnings.append("HTML 块仅保存源码，不执行或解释嵌入内容")
                        location = {"lines": [start + 1, end]}
                        if kind == "list_item" and list_scopes:
                            location["list_scope"] = list_scopes[-1]
                        add(
                            kind,
                            content,
                            location,
                            parent=headings[-1][1] if headings else None,
                            heading=" / ".join(h[2] for h in headings),
                        )
                if any(c.type == "image" for t in tokens for c in (t.children or [])):
                    result.warnings.append("Markdown 图片仅保留替代文本和链接，不识别图像内容")
            else:
                heading = ""
                parent = None
                for m in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", text):
                    if (
                        len(m[0]) < 100
                        and "\n" not in m[0]
                        and re.match(
                            r"^(?:第[一二三四五六七八九十\d]+[章节]|\d+(?:\.\d+)*[、. ]\s*\D)", m[0]
                        )
                    ):
                        heading = m[0]
                        parent = add(
                            "heading", m[0], {"chars": [m.start(), m.end()]}, heading=heading
                        )
                    else:
                        add(
                            "paragraph",
                            m[0],
                            {"chars": [m.start(), m.end()]},
                            heading=heading,
                            parent=parent,
                        )
    except HTTPException:
        raise
    except ImportError:
        raise HTTPException(503, "文档解析依赖未安装") from None
    except (
        ValueError,
        KeyError,
        zipfile.BadZipFile,
        ET.ParseError,
        csv.Error,
        RecursionError,
        RuntimeError,
    ):
        raise HTTPException(422, "文件损坏或格式内容无效") from None
    except Exception:
        if suffix in {".pdf", ".pptx", ".xlsx"}:
            raise HTTPException(422, "文件损坏或格式内容无效") from None
        raise
    if not any(n.text.strip() for n in result.nodes):
        raise HTTPException(422, "文档没有可检索文字")
    result.warnings = list(dict.fromkeys(result.warnings))
    return result
