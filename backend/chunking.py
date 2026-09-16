"""Structure-aware text windows shared by API and model service."""
import re


def chunk_text(body: str, size: int = 220, overlap: int = 30, *, tokenizer=None) -> list[tuple[str, str]]:
    if not 0 <= overlap < size:
        raise ValueError("overlap must be smaller than chunk size")
    page_marker = re.compile(r"(?m)^第 (\d+) 页\s*$")
    pages = list(page_marker.finditer(body))
    if pages:
        located = []
        if body[:pages[0].start()].strip():
            located.extend(chunk_text(body[:pages[0].start()], size, overlap, tokenizer=tokenizer))
        for i, marker in enumerate(pages):
            end = pages[i + 1].start() if i + 1 < len(pages) else len(body)
            for section, fragment in chunk_text(body[marker.end():end], size, overlap, tokenizer=tokenizer):
                located.append((f"第 {marker[1]} 页" + (f" · {section}" if section else ""), fragment))
        return located
    heading = ""
    result: list[tuple[str, str]] = []
    buffer = ""
    # Approximate tokenizer: one CJK character / latin token, preserving source
    # slices. Paragraph boundaries take precedence over fixed-length windows.
    def offsets(value):
        if tokenizer is not None:
            return [start for start, end in tokenizer(value, add_special_tokens=False,
                    return_offsets_mapping=True)["offset_mapping"] if end > start]
        return [part.start() for part in re.finditer(r"[\u4e00-\u9fff]|\w+|[^\w\s]", value)]

    def length(value):
        return len(offsets(value))

    # Isolate Markdown tables, including ones adjacent to ordinary paragraphs.
    body = re.sub(r"(?m)^(?:[ \t]*\|[^\n]*(?:\n|$))+", lambda m: "\n\n" + m[0].strip() + "\n\n", body)
    # Keep headings and numbered steps as boundaries even without blank lines.
    for paragraph in re.split(r"\n\s*\n|\n(?=#{1,6}\s|\d+[.)、]\s*)", body.strip()):
        lines = paragraph.splitlines()
        if len(lines) >= 2 and lines[0].lstrip().startswith("|") and re.fullmatch(r"[\s|:\-]+", lines[1]):
            if buffer:
                result.append((heading, buffer))
                buffer = ""
            header = "\n".join(lines[:2])
            if length(header) < size // 2:
                table = header
                for row in lines[2:]:
                    if length(table + "\n" + row) > size and table != header:
                        result.append((heading, table))
                        table = header
                    if length(header + "\n" + row) > size:
                        for _, fragment in chunk_text(row, size - length(header) - 2, 0, tokenizer=tokenizer):
                            result.append((heading, header + "\n" + fragment))
                    else:
                        table += "\n" + row
                if table != header or len(lines) == 2:
                    result.append((heading, table))
                continue
        if paragraph.startswith("#"):
            if buffer:
                result.append((heading, buffer))
                buffer = ""
            heading = paragraph.splitlines()[0].lstrip("# ")[:500]
        if buffer and length(buffer) > size // 2 and length(buffer + paragraph) > size:
            result.append((heading, buffer))
            parts = offsets(buffer)
            buffer = buffer[parts[max(0, len(parts) - overlap)]:] if overlap and parts else ""
        buffer = (buffer + "\n\n" + paragraph).strip()
        parts = offsets(buffer)
        while len(parts) > size:
            end = parts[size]
            # Prefer complete lines (notably table rows) within the token budget.
            boundary = buffer.rfind("\n", parts[size // 2], end)
            if boundary > 0:
                end = boundary
            result.append((heading, buffer[:end].strip()))
            consumed = sum(start < end for start in parts)
            start = parts[max(1, consumed - overlap)]
            buffer = buffer[start:]
            parts = offsets(buffer)
    if buffer:
        result.append((heading, buffer))
    return result

