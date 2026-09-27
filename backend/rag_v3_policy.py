"""Bounded query strategies and conservative evidence relationships."""
import re
from dataclasses import dataclass

PIPELINE_SCHEMA = 4


def retrieval_question(question):
    # Business users say "attributes" while schemas call them "fields".
    # Apply the same intent expansion to recall and reranking.
    if re.search(r'属性|字段|\b(?:attributes?|properties|fields?)\b', question, re.I):
        return question + ' 字段定义 数据模型 字段字典'
    return question


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
    if re.search(r'全部|所有|列出|有哪些|逐项|\b(?:all|enumerate|list|every)\b', question, re.I):
        types.append('enumeration')
    if re.search(r'条件|例外|除非|限制|至少|至多|\b(?:conditions?|unless|except|requirements?)\b', question, re.I):
        types.append('condition')
    if re.search(r'编号|\$\[|\b[A-Za-z]+[-_]\d+\b|\b\d+(?:\.\d+)+\b', question):
        types.append('identifier')
    if re.search(r'比较|对比|区别|差异|\b(?:compare|versus|vs\.?|difference)\b', question, re.I):
        types.append('comparison')
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
