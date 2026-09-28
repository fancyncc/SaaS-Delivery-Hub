"""Select intact evidence packages before spending the token budget."""
from backend.rag_v3_policy import QueryPolicy, fact_atoms, fact_key


async def assemble_evidence(relevant, budget, count, serialize, policy=None):
    policy = policy or QueryPolicy()
    selected, omitted, conflicts, consumed = [], [], [], set()
    seen, saved, prepared = {}, 0, []
    incomplete_targets = set()
    available_nodes = {h.get('node_id') for h in relevant if h.get('evidence_role') != 'irrelevant'}
    for original in relevant:
        if original['id'] in consumed or original.get('evidence_role') == 'irrelevant':
            continue
        item = dict(original)
        item.setdefault('evidence_role', 'direct')
        if not set(item.get('identity_nodes', [])).issubset(available_nodes):
            original['disposition'] = 'missing_object_identity'
            omitted.append(item['id'])
            continue
        if item.get('location', {}).get('requires_all_parts'):
            members = [h for h in relevant if h['document_id'] == item['document_id'] and h.get('node_id') == item.get('node_id')]
            if len(members) != item['location']['parts'] or not item.get('complete_node_text'):
                original['disposition'] = 'incomplete_structure'
                omitted.append(item['id'])
                incomplete_targets.update(item.get('supports', []))
                continue
            item['text'] = item['complete_node_text']
            item['source_chunk_ids'] = [h['id'] for h in members]
            item['supports'] = list({ref for h in members for ref in h.get('supports', [])})
            if any(h.get('evidence_role', 'direct') == 'direct' for h in members):
                item['evidence_role'] = 'direct'
            consumed.update(item['source_chunk_ids'])
            item['location'] = {**item['location'], 'reassembled': True}
        key = fact_key(item)
        if key in seen:
            original['duplicate_of'] = seen[key]['id']
            original['disposition'] = '重复正文'
            seen[key].setdefault('duplicate_source_ids', []).extend(item['source_chunk_ids'])
            seen[key]['supports'] = list(set(seen[key].get('supports', [])) | set(item.get('supports', [])))
            saved += (await count([serialize(item)]))[0]
            continue
        seen[key] = item
        prepared.append(item)

    # Detect conflicts before budgeting, including evidence omitted afterwards.
    for i, a in enumerate(prepared):
        for b in prepared[i+1:]:
            path = a.get('location', {}).get('path')
            if path and path == b.get('location', {}).get('path') and a['text'] != b['text']:
                conflicts.append([a['id'], b['id']])

    direct = [h for h in prepared if h['evidence_role'] == 'direct']
    # Dependencies form atomic packages: a rule cannot crowd out its exception.
    packages = []
    for core in direct:
        ids = set(core['source_chunk_ids']) | set(core.get('duplicate_source_ids', [])) | {core['id']}
        package = [core]
        # Follow only already identified dependencies, never additional retrieval.
        while True:
            attached = [h for h in prepared if h['id'] not in ids and ids.intersection(h.get('supports', []))]
            if not attached:
                break
            package.extend(attached)
            for h in attached:
                ids.update(h['source_chunk_ids'])
                ids.update(h.get('duplicate_source_ids', []))
                ids.add(h['id'])
        if ids.intersection(incomplete_targets):
            for h in package:
                h['disposition'] = 'incomplete_structure'
                omitted.append(h['id'])
        else:
            packages.append(package)
    packages.sort(key=lambda p: (
        -int(bool(p[0].get('rule_relevance'))),
        -p[0].get('rerank_score', 0),
        p[0]['id'],
    ))
    # Round-robin explicit comparison branches (or source/heading scopes).
    if 'comparison' in policy.types:
        groups = {}
        for package in packages:
            core = package[0]
            branches = core.get('query_branches', [])
            key = next((b for b in branches if b), (core['document_id'], core.get('heading', '')))
            groups.setdefault(key, []).append(package)
        packages = []
        while any(groups.values()):
            for group in groups.values():
                if group:
                    packages.append(group.pop(0))
    selected_ids = set()
    covered = set()
    while packages:
        if 'comparison' not in policy.types:
            # Prefer uncovered literal facts to repeated explanations; relevance
            # breaks ties. Keep raw source text intact rather than synthesizing it.
            packages.sort(key=lambda p: (
                -len(fact_atoms(p[0])-covered)/max(1, len(fact_atoms(p[0]))),
                -int(bool(p[0].get('rule_relevance'))),
                -p[0].get('rerank_score', 0), p[0]['id']))
        package = packages.pop(0)
        extra = [h for h in package if h['id'] not in selected_ids]
        packed = '\n\n'.join(serialize(h) for h in [*selected, *extra])
        units = sum(len(h['source_chunk_ids']) for h in [*selected, *extra])
        if units > policy.unit_limit or (await count([packed]))[0] > budget:
            omitted.extend(h['id'] for h in extra)
            for h in extra:
                h['disposition'] = 'unit_limit' if units > policy.unit_limit else 'token_budget'
            continue
        for h in extra:
            h['selection_reason'] = '保留不同事实及其必要条件、例外和结构上下文；完整性未知'
            h['disposition'] = 'selected'
            selected_ids.add(h['id'])
            if h['evidence_role'] == 'direct':
                covered.update(fact_atoms(h))
            selected.append(h)
    omitted = list(dict.fromkeys(i for i in omitted if i not in selected_ids))
    prepared_by_id = {h['id']: h for h in prepared}
    for original in relevant:
        if original['id'] in prepared_by_id:
            original['disposition'] = prepared_by_id[original['id']].get('disposition', 'support_without_selected_core')
    for item in selected:
        item.pop('complete_node_text', None)
        item.pop('context_anchor', None)
        item.pop('identity_context', None)
        item.pop('identity_nodes', None)
    evidence = '\n\n'.join(serialize(h) for h in selected)
    return selected, evidence, (await count([evidence]))[0], saved, omitted, conflicts
