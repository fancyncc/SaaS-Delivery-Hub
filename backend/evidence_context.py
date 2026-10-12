"""Identify explicit object IDs in bounded structural records, without inference."""

import re
from collections import defaultdict

IDENTITY = re.compile(r"^(?:id|identifier|编号|标识)\s*[:：]\s*([^\n]{1,100})\s*$", re.I)
FIELD = re.compile(r"^[A-Za-z_\u4e00-\u9fff][A-Za-z_\u4e00-\u9fff ]{0,39}\s*[:：]", re.I)
LABEL = re.compile(r"^[A-Za-z_\u4e00-\u9fff]{1,40}$")


def scope(node):
    return (node.parent, *(node.location.get(k) for k in ("page", "slide", "sheet")))


def identity_contexts(index_nodes, original_nodes):
    """Return only original node IDs, never generated evidence text.

    Paragraph records must explicitly label attributes. Vertical two-column
    records must consist entirely of unique field-label/value pairs. Multiple
    IDs, ordinary data tables, prose, and different sections/pages are excluded.
    """
    grouped = defaultdict(list)
    originals = {n.id: n for n in original_nodes}
    for node in index_nodes:
        grouped[scope(node)].append(node)
    output = {}
    for nodes in grouped.values():
        identities = [n for n in nodes if n.kind == "paragraph" and IDENTITY.fullmatch(n.text.strip())]
        if len(identities) == 1:
            anchor = identities[0]
            value = IDENTITY.fullmatch(anchor.text.strip())[1].strip()
            for node in nodes:
                if node.id != anchor.id and node.kind == "paragraph" and len(node.text) <= 600 and FIELD.match(node.text):
                    # Explicit references to other object codes remain separate.
                    codes = re.findall(r"\b[A-Za-z]+[-_]\d+[A-Za-z0-9_-]*\b", node.text)
                    if all(code.casefold() == value.casefold() for code in codes):
                        output[node.id] = [anchor.id]
        records = [n for n in nodes if n.kind == "table_record"]
        pairs = []
        for node in records:
            cells = [originals[c] for c in node.location.get("cell_nodes", []) if c in originals]
            if len(cells) != 2 or not LABEL.fullmatch(cells[0].text.strip()) or len(cells[1].text) > 600:
                pairs = []
                break
            pairs.append((node, cells[0].text.strip(), cells[1].text.strip()))
        labels = [label.casefold() for _, label, _ in pairs]
        anchors = [n for n, label, value in pairs if label.casefold() in {"id", "identifier", "编号", "标识"} and value and len(value) <= 100 and "\n" not in value]
        if len(pairs) >= 3 and len(labels) == len(set(labels)) and len(anchors) == 1:
            anchor = anchors[0]
            for node, _, value in pairs:
                if node.id != anchor.id and not re.search(r"\b[A-Za-z]+[-_]\d+", value):
                    output[node.id] = [anchor.id]
    return output


def ranking_text(hit):
    # The projection is only for ranking. Citations and returned body stay original.
    context = "\n".join(dict.fromkeys(value for value in (
        hit.get("heading", ""),
        hit.get("identity_context", ""), hit.get("context_anchor", ""),
    ) if value))
    return (context[:1000] + "\n" if context else "") + hit["text"]
