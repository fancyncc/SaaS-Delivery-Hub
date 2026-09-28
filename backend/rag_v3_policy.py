"""Bounded query strategies and conservative evidence relationships."""
import re
from dataclasses import dataclass

PIPELINE_SCHEMA = 5

# Deterministic retrieval-only translations for common cross-language source
# terminology.  The original question and source text remain unchanged in the
# evidence and citations.
CROSS_LANGUAGE_TERMS = (
    (re.compile(r"硬编码", re.I), ("hardcoded",)),
    (re.compile(r"秘密|机密|凭据", re.I), ("secrets",)),
    (re.compile(r"静态分析", re.I), ("static analysis",)),
    (re.compile(r"测试结果", re.I), ("test results",)),
)

OBJECT_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+(?![A-Za-z0-9])"
)


def query_identifiers(question):
    """Return explicit business object codes, excluding ordinary words and versions."""
    return tuple(dict.fromkeys(match.group(0) for match in OBJECT_IDENTIFIER.finditer(question)))


def object_identifiers(hit):
    """Read only identifiers explicitly attached to an object label or JSON root."""
    output = []
    path = str(hit.get("location", {}).get("path", ""))
    path_parts = re.findall(r'\["([^"\n]+)"\]', path)
    if path_parts and OBJECT_IDENTIFIER.fullmatch(path_parts[0]):
        output.append(path_parts[0])
    lines = "\n".join(
        str(value) for value in (hit.get("identity_context", ""), hit.get("text", "")) if value
    ).splitlines()
    for index, line in enumerate(lines):
        match = re.fullmatch(
            rf"\s*(?:id|identifier|编号|标识)\s*[:：]\s*({OBJECT_IDENTIFIER.pattern})\s*",
            line,
            re.I,
        )
        if match:
            output.append(match.group(1))
            continue
        if not re.fullmatch(r"\s*(?:id|identifier|编号|标识)\s*", line, re.I):
            continue
        for following in lines[index + 1 : index + 4]:
            found = OBJECT_IDENTIFIER.search(following)
            if found:
                output.append(found.group(0))
                break
    return tuple(dict.fromkeys(value.casefold() for value in output))


def document_object_identifiers(candidates):
    output = {}
    for hit in candidates:
        output.setdefault(hit["document_id"], set()).update(object_identifiers(hit))
    return output


def identifier_documents(question, candidates):
    """Use an exact object code to narrow documents before semantic reranking.

    A matching unit only identifies the authorized source document. It is not
    returned as evidence unless it independently passes relevance/structure
    checks, and no relationship between separate source locations is invented.
    """
    identifiers = query_identifiers(question)
    if not identifiers:
        return None
    wanted = {identifier.casefold() for identifier in identifiers}
    matched = {
        document_id
        for document_id, available in document_object_identifiers(candidates).items()
        if wanted & available
    }
    return matched or None


def identifier_matches(question, hit, identifiers_by_document):
    wanted = {identifier.casefold() for identifier in query_identifiers(question)}
    if not wanted:
        return True
    attached = set(object_identifiers(hit))
    if attached:
        return bool(wanted & attached)
    document_ids = identifiers_by_document.get(hit["document_id"], set())
    # Unlabelled text is safe only when the source has one explicit object scope
    # (or all explicit scopes were named by a comparison query).
    return bool(document_ids) and document_ids <= wanted


def requested_labels(question, policy):
    labels = set()
    if re.search(r"属性|字段|\b(?:attributes?|properties|fields?)\b", question, re.I):
        labels.update(("fields", "field", "attributes", "properties", "字段", "属性"))
    if "condition" in policy.types:
        labels.update(
            ("conditions", "condition", "exceptions", "exception", "requirements", "restrictions", "条件", "例外", "限制")
        )
    if re.search(r"上限|限额|额度|\b(?:limit|quota|maximum|max)\b", question, re.I):
        labels.update(("limit", "quota", "maximum", "max", "上限", "限额", "额度"))
    return labels


def explicit_structure_match(question, hit, policy):
    """Match a requested field only when its source label is explicit."""
    labels = requested_labels(question, policy)
    if not labels:
        return False
    path = str(hit.get("location", {}).get("path", ""))
    path_labels = {value.casefold() for value in re.findall(r'\["([^"\n]+)"\]', path)}
    if path_labels & {label.casefold() for label in labels}:
        return True
    text = hit.get("text", "")
    return any(
        re.search(rf"(?im)^\s*{re.escape(label)}\s*(?::|：|$)", text)
        for label in labels
    )


def cross_language_terms(question):
    """Return only glossary terms explicitly triggered by the question."""
    return tuple(
        dict.fromkeys(
            term
            for pattern, terms in CROSS_LANGUAGE_TERMS
            if pattern.search(question)
            for term in terms
        )
    )


def literal_cross_language_match(question, hit):
    """Require every triggered concept to occur literally in the source."""
    terms = cross_language_terms(question)
    if not terms:
        return False
    source = f"{hit.get('heading', '')}\n{hit.get('text', '')}".casefold()
    return all(term.casefold() in source for term in terms)


def explicit_priority_match(question, hit):
    """Recognize an explicit source ordering for an ordering question."""
    if not re.search(r"优先|优先级|顺序|先后|\b(?:priority|precedence|order)\b", question, re.I):
        return False
    source = hit.get("text", "")
    term = r"[\w\u4e00-\u9fff`.-]+"
    return bool(re.search(rf"{term}(?:\s*>\s*{term}){{2,}}", source))


def retrieval_question(question):
    # Business users say "attributes" while schemas call them "fields".
    # Apply the same intent expansion to recall and reranking.
    additions = []
    if re.search(r'属性|字段|\b(?:attributes?|properties|fields?)\b', question, re.I):
        additions.extend(('字段定义', '数据模型', '字段字典'))
    additions.extend(cross_language_terms(question))
    return question + (' ' + ' '.join(additions) if additions else '')


@dataclass(frozen=True)
class QueryPolicy:
    types: tuple[str, ...] = ('fact',)
    queries: tuple[str, ...] = ()
    recall_limit: int = 30
    rerank_limit: int = 32
    unit_limit: int = 8
    repair_limit: int = 12

    @property
    def enumeration(self):
        return 'enumeration' in self.types


def query_policy(question):
    types = []
    if re.search(r'全部|所有|列出|有哪些|哪些|逐项|\b(?:all|enumerate|list|every)\b', question, re.I) or re.search(
        r'\bwhat\s+(?:should|must)\b[^?]*\band\b', question, re.I
    ):
        types.append('enumeration')
    if re.search(r'条件|例外|除非|限制|至少|至多|\b(?:conditions?|unless|except|requirements?)\b', question, re.I):
        types.append('condition')
    if re.search(r'编号|\$\[|\b[A-Za-z]+[-_]\d+\b|\b\d+(?:\.\d+)+\b', question):
        types.append('identifier')
    if re.search(r'比较|对比|区别|差异|\b(?:compare|versus|vs\.?|difference)\b', question, re.I):
        types.append('comparison')
    if re.search(r'优先|优先级|顺序|先后|\b(?:priority|precedence|order)\b', question, re.I):
        types.append('priority')
    # Preserve the original query; split only explicitly named comparison sides.
    queries = [question]
    expanded = retrieval_question(question)
    if expanded != question:
        queries.append(expanded)
    if 'condition' in types:
        queries.append(question + ' 适用条件 例外 限制 exceptions restrictions')
    sides = re.findall(r'[“「"]([^”」"\n]+)[”」"]', question)
    if 'comparison' in types:
        if len(sides) != 2:
            match = re.search(r'(?:比较|对比|compare)\s*(.+?)\s*(?:与|和|及|versus|vs\.?|and)\s*(.+?)(?:的|[?？]|$)', question, re.I)
            sides = list(match.groups()) if match else []
        if len(sides) == 2:
            queries = [question, *(f'{side.strip()} {question}' for side in sides)]
    broad = 'enumeration' in types or 'comparison' in types
    return QueryPolicy(tuple(types or ['fact']), tuple(queries), 60 if broad else 30,
                       96 if broad else 32, 64 if broad else 8,
                       64 if 'enumeration' in types else 12)


def structural_relation(core, other, *, enumeration=False):
    if core['document_id'] != other['document_id'] or core['id'] == other['id']:
        return None
    if core.get('node_id') and core['node_id'] == other.get('node_id'):
        return 'node_parts'
    if other.get('node_id') in core.get('identity_nodes', []):
        return 'object_identity'
    if enumeration and core['kind'] == other['kind'] == 'json_value' and core.get('parent') is not None and core['parent'] == other.get('parent'):
        return 'enumeration'
    if core.get('record') and core['record'] == other.get('record'):
        return 'record'
    refs = re.findall(r'(?:参见|见章节|see section)\s*[“「"]([^”」"\n]+)[”」"]', core['text'], re.I)
    if other.get('heading') in refs:
        return 'explicit_reference'
    reverse_refs = re.findall(r'(?:参见|见章节|see section)\s*[“「"]([^”」"\n]+)[”」"]', other['text'], re.I)
    if core.get('heading') in reverse_refs:
        return 'explicit_reference'
    if enumeration:
        a, b = core.get('location', {}), other.get('location', {})
        if a.get('list_scope') and a.get('list_scope') == b.get('list_scope'):
            return 'enumeration'
        if core['kind'] in {'table_cell', 'table_record'} and other['kind'] in {'table_cell', 'table_record'}:
            def table_key(item):
                record = item.get('record') or ''
                return record.rsplit(':row:', 1)[0] if ':row:' in record else 'csv' if record.startswith('row:') else None
            if table_key(core) and table_key(core) == table_key(other):
                return 'enumeration'
        if core['kind'] == other['kind'] == 'json_value' and core.get('parent') is not None and core['parent'] == other.get('parent'):
            return 'enumeration'
    return None


def fact_key(item):
    # Only exact normalized facts in the same source and semantic scope merge.
    # Numbers, negation, conditions, source version and JSON paths remain intact.
    return (item['document_id'], item.get('version'), item.get('heading'),
            item.get('parent'), item.get('record'), item.get('location', {}).get('path'),
            re.sub(r'\s+', ' ', item['text']).strip())


def fact_atoms(item):
    """Literal fact coverage only: never equate paraphrases or erase negation."""
    scope = fact_key(item)[:-1]
    parts = re.split(r'(?<=[。！？.!?;；])\s*|\n+', item['text'])
    return {(*scope, re.sub(r'\s+', ' ', part).strip()) for part in parts if part.strip()}
