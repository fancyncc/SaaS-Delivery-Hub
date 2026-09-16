"""Populate a dedicated local demo via application APIs; never resets existing data.

Run from the repository root: .venv/Scripts/python.exe -m scripts.seed_demo_workspace
Generated credentials, documents and manifests stay in the git-ignored data directory.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from sqlalchemy.engine import make_url

from scripts.demo_documents import (
    COMPANY,
    PROJECTS,
    SLUG,
    bad_members_csv,
    members_csv,
    project_payload,
    write_files,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "demo_workspace"
ROLES = [
    ("admin", "demo_admin", "林若溪（演示管理员）", "company_admin"),
    ("consultant", "demo_consultant", "陆远舟（演示顾问）", "implementation_consultant"),
    ("approver", "demo_approver", "沈知夏（演示审批人）", "approver"),
    ("viewer", "demo_viewer", "苏沐（演示只读）", "viewer"),
]
LABELS = {"draft": "草稿，可启动", "plan": "待计划审批", "configuration": "待配置审批",
          "materials": "等待修正导入材料", "acceptance": "待上线验收审批", "completed": "已完成交付"}


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare():
    os.chdir(ROOT)
    from backend.config import get_settings
    settings = get_settings()
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise RuntimeError("This seed command only supports an on-disk local SQLite database")
    database = Path(url.database).resolve()
    if not database.is_relative_to(ROOT):
        raise RuntimeError("Configured database is outside the project; refusing to write")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if database.exists():
        backups = ROOT / "backups"
        backups.mkdir(exist_ok=True)
        backup = backups / f"before-demo-{datetime.now(UTC):%Y%m%d-%H%M%S-%f}.sqlite3"
        with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as source, sqlite3.connect(backup) as target:
            source.backup(target)
        print(f"Backup: {backup}", flush=True)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True)
    # Only this seed process uses these settings. No SMTP/model/network calls.
    settings.mail_debug = True
    settings.model_mode = "deterministic"
    settings.embedding_model = ""
    settings.reranker_model = ""
    settings.execution_mode = "inline"
    settings.agent_engine = "legacy"
    settings.storage_backend = "database"
    settings.cookie_secure = False
    settings.frontend_base_url = "http://127.0.0.1:8000"
    credentials_file = OUTPUT / "credentials.json"
    if credentials_file.exists():
        credentials = json.loads(credentials_file.read_text(encoding="utf-8"))
    else:
        credentials = {key: {"username": username, "display_name": name, "role": role,
                            "email": f"{username}@xinglan.example.test", "password": "Demo!" + secrets.token_urlsafe(12) + "9a"}
                       for key, username, name, role in ROLES}
        save_json(credentials_file, credentials)
    manifest_file = OUTPUT / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8")) if manifest_file.exists() else {"seed_id": str(uuid4()), "company": COMPANY, "projects": {}, "chats": {}}
    save_json(manifest_file, manifest)
    return credentials, manifest, write_files(OUTPUT)


async def seed(credentials, manifest, documents):
    from backend.db import SessionLocal, bootstrap_identity, engine
    from backend.knowledge import retrieve
    from backend.main import app

    await bootstrap_identity()
    clients = {key: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://demo.local") for key, *_ in ROLES}
    admin, consultant, approver = clients["admin"], clients["consultant"], clients["approver"]

    def checkpoint():
        save_json(OUTPUT / "manifest.json", manifest)

    async def call(client, method, path, *, payload=None, files=None, expected=200):
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload is not None else ""
        key = str(uuid5(NAMESPACE_URL, manifest['seed_id'] + method + path + body))
        headers = {"X-CSRF-Token": client.cookies.get("saas_csrf", ""), "Idempotency-Key": key}
        response = await client.request(method, path, json=payload, files=files, headers=headers)
        if response.status_code != expected:
            # Do not include submitted payload or credentials in diagnostics.
            raise RuntimeError(f"{method} {path}: {response.status_code}; {response.text[:600]}")
        return response.json().get("data", response.json())

    async def login_or_register_main():
        account = credentials['admin']
        response = await admin.post('/api/auth/login', json={"username": account['username'], "password": account['password']})
        if response.status_code == 401:
            await call(admin, 'POST', '/api/auth/register', payload={"username": account['username'], "password": account['password'], "display_name": account['display_name']})
        elif response.status_code != 200:
            raise RuntimeError(f"Main login failed: HTTP {response.status_code}")
        profile = await call(admin, 'GET', '/api/auth/profile')
        if not profile['email_verified']:
            sent = await call(admin, 'POST', '/api/auth/profile/email', payload={"email": account['email'], "current_password": account['password']})
            token = parse_qs(urlparse(sent['verification_url']).query)['token'][0]
            await call(admin, 'POST', '/api/auth/verify-email', payload={"token": token})
        spaces = await call(admin, 'GET', '/api/auth/spaces')
        company = next((s for s in spaces if s['tenant_name'] == COMPANY), None)
        if company:
            tenant_id = company['tenant_id']
        else:
            tenant_id = (await call(admin, 'POST', '/api/companies', payload={"name": COMPANY, "slug": SLUG}))['id']
        await call(admin, 'POST', f'/api/auth/spaces/{tenant_id}/switch')
        manifest['tenant_id'] = tenant_id
        checkpoint()

    try:
        await login_or_register_main()
        print("Main account and dedicated company ready", flush=True)
        user_ids = {'admin': (await call(admin, 'GET', '/api/auth/me'))['id']}
        for key, *_ in ROLES[1:]:
            account, client = credentials[key], clients[key]
            response = await client.post('/api/auth/login', json={"username": account['username'], "password": account['password']})
            if response.status_code == 401:
                invitation = await call(admin, 'POST', '/api/company/invitations', payload={"email": account['email'], "display_name": account['display_name'], "company_role": "tenant_member", "tenant_id": manifest['tenant_id']})
                token = parse_qs(urlparse(invitation['invitation_url']).query)['token'][0]
                await call(client, 'POST', f'/api/auth/invitations/{token}/accept', payload={"username": account['username'], "password": account['password'], "display_name": account['display_name']})
            elif response.status_code != 200:
                raise RuntimeError(f"Role login failed: {key}")
            await call(client, 'POST', f"/api/auth/spaces/{manifest['tenant_id']}/switch")
            user_ids[key] = (await call(client, 'GET', '/api/auth/me'))['id']
        manifest['users'] = user_ids
        checkpoint()
        existing = {p['name']: p for p in await call(admin, 'GET', '/api/projects')}
        for p in PROJECTS:
            code = p['code']
            entry = manifest['projects'].get(code)
            if not entry:
                payload = project_payload(p)
                project = existing.get(payload['name']) or await call(admin, 'POST', '/api/projects', payload=payload)
                entry = manifest['projects'][code] = {'id': project['id'], 'name': payload['name'], 'target': p['stop']}
                checkpoint()
            pid = entry['id']
            members = {m['user_id'] for m in await call(admin, 'GET', f'/api/projects/{pid}/members')}
            for key, *_ in ROLES[1:]:
                if user_ids[key] not in members:
                    await call(admin, 'POST', f'/api/projects/{pid}/members', payload={"user_id": user_ids[key], "primary_role_code": credentials[key]['role']})
            if not entry.get('prepared') and p['stop'] != 'draft':
                current = await call(consultant, 'GET', f'/api/projects/{pid}')
                run_id = (current.get('latest_run') or {}).get('id')
                if not run_id:
                    run_id = (await call(consultant, 'POST', f'/api/projects/{pid}/runs'))['id']
                entry['run_id'] = run_id
                checkpoint()
                for _ in range(12):
                    run = await call(consultant, 'GET', f'/api/runs/{run_id}')
                    if run['status'] == 'succeeded':
                        if p['stop'] != 'completed':
                            raise RuntimeError(f"Unexpected completion: {code}")
                        break
                    if run['status'] == 'preparing_materials':
                        text = bad_members_csv() if p['stop'] == 'materials' else members_csv(p)
                        validation = await call(consultant, 'POST', '/api/imports/validate', payload={"project_id": pid, "run_id": run_id, "csv_text": text})
                        entry['import_job_id'] = validation['job_id']
                        if p['stop'] == 'materials':
                            if validation['valid']:
                                raise RuntimeError("Invalid fixture was unexpectedly accepted")
                            entry['validation_errors'] = validation['errors']
                            break
                        if not validation['valid']:
                            raise RuntimeError(f"Valid fixture failed: {code}; {validation['errors']}")
                        continue
                    if run['status'] != 'waiting_approval':
                        raise RuntimeError(f"Unexpected Run state {code}: {run['status']}")
                    approvals = await call(approver, 'GET', f'/api/approvals?run_id={run_id}')
                    pending = next(a for a in approvals if a['status'] == 'pending')
                    if pending['kind'] == p['stop']:
                        entry['pending_approval'] = pending['id']
                        break
                    await call(approver, 'POST', f"/api/approvals/{pending['id']}/decision", payload={"decision": "approved", "expected_version": pending['version'], "comment": f"虚构演示 {code}：独立审批人已核对本轮{pending['kind']}材料，仅批准本地模拟工作区测试。"})
                else:
                    raise RuntimeError(f"Workflow did not reach desired state: {code}")
            entry['prepared'] = True
            checkpoint()
            print(f"{code}: {LABELS[p['stop']]}", flush=True)

        # Publish authored materials only after workflow fixtures are built, so
        # deterministic workflow examples do not depend on newly authored prose.
        existing_docs = {(d['title'], d['version']): d for d in await call(admin, 'GET', '/api/knowledge')}
        manifest['documents'] = []
        for doc in documents:
            item = existing_docs.get((doc['title'], doc['version']))
            if not item:
                item = await call(admin, 'POST', '/api/knowledge', payload={"project_id": manifest['projects'][doc['project']]['id'] if doc.get('project') else None, "title": doc['title'], "version": doc['version'], "module": doc['module'], "source": f"虚构测试文档/{doc['key']}", "license": "项目内虚构演示资料，可用于本地测试", "body": doc['body']})
            if not doc['active'] and item.get('active', True):
                await call(admin, 'POST', f"/api/knowledge/{item['id']}/deactivate")
            manifest['documents'].append({'id': item['id'], 'key': doc['key'], 'title': doc['title'], 'version': doc['version'], 'active': doc['active']})
            checkpoint()
        print(f"Published {len(documents)} authored documents", flush=True)

        memory = await call(admin, 'GET', '/api/chat/memory')
        if not memory['content']:
            await call(admin, 'PUT', '/api/chat/memory', payload={"expected_version": memory['version'], "content": "我是星澜演示公司的实施项目负责人。请用中文，先给结论，再列出依据、风险和下一步。涉及项目时注明 XL 项目编号；涉及数字和日期时引用资料。所有测试公司、人物和业务数据都是虚构设定。不要把计划目标说成已执行结果。"})
        chat_specs = [
            ('company', None, ['资料分类与数据安全规范最新的资料复核周期是多少天？', '公司文档里的组织架构与职责分工是什么？']),
            ('migration', 'XL-104', ['XL-104 成员导入有哪些校验规则，正确文件应该有多少人？', '如果出现重复邮箱和非法角色，应该由谁处理？']),
            ('acceptance', 'XL-105', ['XL-105 财务协作项目的验收标准是什么？', '完成验收以后应该有哪些交付材料？']),
            ('completed', 'XL-106', ['XL-106 项目的成员规模和交付物有哪些？', '这个项目包含真实工资和社保申报吗？']),
            ('private', None, ['演示个人偏好的汇报格式是什么？', '总结我上传的演示个人偏好说明。']),
        ]
        for key, code, questions in chat_specs:
            chat_id = manifest['chats'].get(key)
            if chat_id:
                chat = await call(admin, 'GET', f'/api/chat/conversations/{chat_id}')
            else:
                chat = await call(admin, 'POST', '/api/chat/conversations', payload={"project_id": manifest['projects'][code]['id'] if code else None})
                chat_id = manifest['chats'][key] = chat['id']
                checkpoint()
            if not chat['documents'] and key in {'migration', 'private'}:
                if key == 'migration':
                    attachments = [('XL-104_错误样本.csv', bad_members_csv()), ('XL-104_迁移手册.md', next(d['body'] for d in documents if d['key'] == 'XL-104-02'))]
                else:
                    attachments = [('演示个人偏好.txt', '演示个人偏好说明：汇报采用“结论、证据、风险、下一步”四段格式。只在这段私有对话里使用的小组代号为橙色纸飞机。所有信息都是虚构偏好，不包含密码或真实个人信息。')]
                for name, body in attachments:
                    chat = await call(admin, 'POST', f"/api/chat/conversations/{chat_id}/documents?expected_version={chat['version']}", files={'file': (name, body.encode('utf-8'), 'text/plain')})
            for question in questions:
                if any(m['question'] == question for m in chat['messages']):
                    continue
                chat = await call(admin, 'POST', f'/api/chat/conversations/{chat_id}/messages', payload={"question": question, "expected_version": chat['version'], "request_id": str(uuid5(NAMESPACE_URL, manifest['seed_id'] + key + question))})
                if not chat['messages'][-1]['citations']:
                    raise RuntimeError(f"Seeded chat has no evidence: {key}")
        print("Five private conversations and cross-conversation memory ready", flush=True)

        manifest['verification'] = []
        total_members, total_artifacts = 0, 0
        for p in PROJECTS:
            entry = manifest['projects'][p['code']]
            current = await call(admin, 'GET', f"/api/projects/{entry['id']}")
            details = await call(admin, 'GET', f"/api/projects/{entry['id']}/delivery")
            count = len(details['members'])
            expected = p['count'] if p['stop'] in {'acceptance', 'completed'} else 0
            if count != expected:
                raise RuntimeError(f"Member count mismatch: {p['code']} {count} != {expected}")
            total_members += count
            total_artifacts += len(details['artifacts'])
            entry['actual_status'] = current['execution_status'] or current['lifecycle_status']
            entry['actual_members'] = count
            entry['actual_artifacts'] = len(details['artifacts'])
            manifest['verification'].append({'project': p['code'], 'status': entry['actual_status'], 'members': count, 'artifacts': entry['actual_artifacts']})
        async with SessionLocal() as session:
            security_hits = await retrieve(session, manifest['tenant_id'], '资料分类 数据安全 资料复核周期 90 天', limit=5)
            current_id = next(d['id'] for d in manifest['documents'] if d['key'] == 'C04')
            old_id = next(d['id'] for d in manifest['documents'] if d['key'] == 'C04-old')
            if not any(h.get('document_id') == current_id for h in security_hits) or any(h.get('document_id') == old_id for h in security_hits):
                raise RuntimeError('Knowledge version verification failed')
            if await retrieve(session, manifest['tenant_id'], 'RETIRED-CANARY-7319'):
                raise RuntimeError('Inactive document was retrieved')
        denied = await clients['viewer'].get('/api/chat/conversations/' + manifest['chats']['private'])
        if denied.status_code != 404:
            raise RuntimeError('Private chat visibility verification failed')
        manifest['totals'] = {'accounts': 4, 'projects': len(PROJECTS), 'documents': len(documents), 'actual_business_members': total_members, 'artifacts': total_artifacts, 'conversations': len(chat_specs), 'csv_samples': 7}
        manifest['completed_at'] = datetime.now(UTC).isoformat()
        checkpoint()
        write_guide(credentials, manifest)
        print(json.dumps(manifest['totals'], ensure_ascii=False), flush=True)
        print(f"Guide: {OUTPUT / '测试指南.md'}", flush=True)
    finally:
        for client in clients.values():
            await client.aclose()
        await engine.dispose()


def write_guide(credentials, manifest):
    lines = ["# 星澜演示空间测试指南", "", "> 全部公司、人员与业务内容均为虚构测试数据。数据通过应用接口写入本地数据库，未发出真实邮件，未调用外部模型。", "",
             "## 登录", "", "入口：http://127.0.0.1:8000/login。请使用客户登录入口，登录后选择“星澜智造科技有限公司（演示）”公司空间。", "",
             "账号密码见同目录 `credentials.json` 和 `测试账号.md`。凭据未上传知识库。", "", "## 项目场景", "",
             "| 项目 | 预置阶段 | 实际业务成员 | 实际交付物 | 推荐操作 |", "|---|---|---:|---:|---|"]
    actions = {"draft": "管理员或顾问启动，观察计划生成", "plan": "用审批人账号审阅并批准或拒绝计划", "configuration": "核对配置差异，再批准配置", "materials": "顾问查看错误报告，上传正确 40 人 CSV", "acceptance": "审批人完成最后验收，观察结项", "completed": "下载六类交付物，查看 Trace 和成员"}
    for p in PROJECTS:
        entry = manifest['projects'][p['code']]
        lines.append(f"| {p['code']} {p['name']} | {LABELS[p['stop']]} | {entry['actual_members']} | {entry['actual_artifacts']} | {actions[p['stop']]} |")
    lines += ["", "公司后台有 4 个可登录成员；已经导入的 54 名业务成员属于内置模拟 SaaS，不能用这些业务邮箱登录实施平台。公司文档中 240 人为虚构业务背景，项目名册人数允许重叠。", "",
              "## 文档包", "", "公司制度和项目材料共 36 份 Markdown，另有 6 份项目字段 JSON。每个项目各有立项需求、迁移手册、验收风险清单、启动纪要与 FAQ 四份文档。共享知识库已录入这些资料并建立检索索引。", "",
              "其中《资料分类与数据安全规范》有 v1/v2，最新为 v2（90 天复核），旧版仅供版本过滤测试；另有 1 份停用资料用于验证停用不召回。不要把制度中的复核周期误认为系统回收站保留期。", "",
              "## AI 助手建议问题", ""]
    for question in ['资料复核周期是多少天？请给出来源。', *[p['question'] for p in PROJECTS], 'CSV 的业务角色和项目中的实施角色有什么区别？', '公司实际的银行账号和生产数据库密码是什么？（预期：没有资料，不能编造）']:
        lines.append(f"- {question}")
    lines += ["", "管理员已准备 5 段对话及 10 轮有引用的问答，含 3 份私有附件。长期记忆保存了中文、先结论、列证据的偏好。用只读账号无法读取管理员的私有会话；企业知识仍按当前公司权限可见。", "",
              "当前模型未配置，问答显示匹配原文。要测试真正的综合回答和记忆对输出的影响，需要配置真实模型；现有历史不会自动改写成模型答案。", "",
              "## 导入测试", "", "`导入样本/` 包含六个项目的正确 CSV 以及 XL-104 错误 CSV。XL-104 当前保留无效导入任务，顾问可下载错误报告，再提交 XL-104_members_valid.csv；请勿交叉使用不同项目的名单。", "",
              "## 数据与重跑", "", "数据写入项目根目录 saas_agent.db。原数据库备份在 backups/before-demo-*.sqlite3。不要运行 pytest 来生成展示数据：测试套件使用独立数据库。", "",
              "`python -m scripts.seed_demo_workspace` 会依据本目录 manifest 和账号文件续跑，不清空已有业务。为了保留你测试后的状态，不会重新推进已标记准备完成的项目。若你已改变人数或删除记录，重跑验证可能提示差异，不会自动重置。", "",
              "## 本次校验", "", "已核对登录、四角色项目权限、各项目实际状态和成员数、交付物数量、知识新旧版本过滤、停用资料不召回、私有会话不可越权读取。所有已生成问答均有实际检索来源。", ""]
    (OUTPUT / '测试指南.md').write_text('\n'.join(lines), encoding='utf-8')
    accounts = ['# 本地演示测试账号', '', '> 仅用于本项目测试；此文件未提交 Git，也未放入共享知识库。', '', '| 用途 | 账号 | 密码 |', '|---|---|---|']
    for key, *_ in ROLES:
        a = credentials[key]
        accounts.append(f"| {a['display_name']} | `{a['username']}` | `{a['password']}` |")
    accounts += ['', '主账号为 demo_admin。所有账号均通过客户入口登录，辅助账号用于独立审批、导入和只读权限测试。', '']
    (OUTPUT / '测试账号.md').write_text('\n'.join(accounts), encoding='utf-8')


if __name__ == '__main__':
    asyncio.run(seed(*prepare()))
