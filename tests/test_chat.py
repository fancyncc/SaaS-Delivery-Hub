import io
import uuid
import zipfile

import httpx
import pytest
from fastapi import HTTPException

from backend import intelligence
from backend.chat import ChatAnswer, conversation_context, extract_document
from backend.config import get_settings
from tests.test_api import project, role_client


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(get_settings(), "model_mode", "deterministic")
    monkeypatch.setattr(get_settings(), "embedding_model", "")


async def new_chat(client, project_id=None):
    response = await client.post("/api/chat/conversations", json={"project_id": project_id})
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def upload(client, chat, body="上线验收要求：至少导入 80 名成员，必须有管理员。", name="guide.md"):
    response = await client.post(f"/api/chat/conversations/{chat['id']}/documents", params={"expected_version": chat["version"]}, files={"file": (name, body.encode(), "text/plain")})
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def send(client, chat, question="上线验收要求是什么？", request_id=None, **extra):
    return await client.post(f"/api/chat/conversations/{chat['id']}/messages", json={"question": question, "expected_version": chat["version"], "request_id": request_id or str(uuid.uuid4()), **extra})


async def test_upload_retrieve_persist_idempotency_delete(client):
    chat = await upload(client, await new_chat(client))
    request_id = str(uuid.uuid4())
    response = await send(client, chat, request_id=request_id)
    assert response.status_code == 200, response.text
    saved = response.json()["data"]
    assert "80" in saved["messages"][0]["answer"]
    assert saved["messages"][0]["citations"][0]["title"] == "guide.md"
    replay = await send(client, chat, request_id=request_id)
    assert replay.status_code == 200
    assert len(replay.json()["data"]["messages"]) == 1
    assert (await send(client, chat)).status_code == 409
    assert (await client.get(f"/api/chat/conversations/{chat['id']}")).json()["data"] == saved
    deleted = await client.delete(f"/api/chat/conversations/{chat['id']}/documents/{saved['documents'][0]['id']}", params={"expected_version": saved["version"]})
    response = await send(client, deleted.json()["data"])
    assert all(c['kind'] == 'platform_manual' for c in response.json()["data"]["messages"][-1]["citations"])
    assert (await client.delete(f"/api/chat/conversations/{chat['id']}")).status_code == 200
    assert (await client.get(f"/api/chat/conversations/{chat['id']}")).status_code == 404


async def test_private_scope_project_revocation_and_platform(client, platform_client):
    p = await project(client)
    chat = await new_chat(client, p["id"])
    response = await send(client, chat, "项目的部门和培训要求？")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["messages"][0]["citations"]
    member = await role_client(client, p["id"], "viewer", "chat-viewer@example.com")
    try:
        assert (await member.get(f"/api/chat/conversations/{chat['id']}")).status_code == 404
        assert (await member.get('/api/chat/conversations')).json()['data']['conversations'] == []
    finally:
        await member.aclose()
    assert (await platform_client.get('/api/chat/conversations')).status_code == 403
    # Soft deletion revokes chat access too, even when cached messages exist.
    assert (await client.delete(f"/api/projects/{p['id']}", headers={"Idempotency-Key": str(uuid.uuid4())})).status_code == 200
    assert (await client.get(f"/api/chat/conversations/{chat['id']}")).status_code == 404


async def test_history_search_content_recency_and_privacy(client):
    first = await upload(client, await new_chat(client), '验收包含独特标记 HISTORY-CANARY-723，必须完成培训。')
    first = (await send(client, first, '验收要求是什么')).json()['data']
    second = (await send(client, await new_chat(client), '另一个项目的问题')).json()['data']
    rows = (await client.get('/api/chat/conversations')).json()['data']['conversations']
    assert rows[0]['id'] == second['id'] and rows[0]['updated_at']
    assert rows[0]['message_count'] == 1
    rows = (await client.get('/api/chat/conversations', params={'q': 'HISTORY-CANARY-723'})).json()['data']['conversations']
    assert [r['id'] for r in rows] == [first['id']]
    await send(client, first, '继续解释验收要求')
    rows = (await client.get('/api/chat/conversations')).json()['data']['conversations']
    assert rows[0]['id'] == first['id'] and rows[0]['message_count'] == 2
    p = await project(client)
    member = await role_client(client, p['id'], 'viewer', 'history-viewer@example.com')
    try:
        result = await member.get('/api/chat/conversations', params={'q': 'HISTORY-CANARY-723'})
        assert result.json()['data']['conversations'] == []
    finally:
        await member.aclose()


async def test_llm_context_memory_and_failure_are_transactional(client, monkeypatch):
    saved = await client.put('/api/chat/memory', json={"content": "请用中文，先给结论", "expected_version": 0})
    assert saved.status_code == 200
    assert (await client.put('/api/chat/memory', json={"content": "stale", "expected_version": 0})).status_code == 409
    chat = await upload(client, await new_chat(client))
    captured = []

    async def fake(task, source, schema):
        captured.append(source)
        return ChatAnswer(answer="需导入 80 名成员。[1]", citation_ids=[source['evidence'][0]['id']])

    monkeypatch.setattr(get_settings(), "model_mode", "real")
    monkeypatch.setattr(intelligence, "structured", fake)
    first = await send(client, chat)
    assert first.status_code == 200, first.text
    chat = first.json()['data']
    second = await send(client, chat, "那管理员呢？")
    assert second.status_code == 200, second.text
    assert captured[-1]['memory'] == "请用中文，先给结论"
    assert any(m['role'] == 'assistant' for m in captured[-1]['context']['recent_messages'])
    chat = second.json()['data']

    async def invalid(task, source, schema):
        return ChatAnswer(answer="假引用[1]", citation_ids=['invented'])

    monkeypatch.setattr(intelligence, 'structured', invalid)
    assert (await send(client, chat)).status_code == 422
    assert (await client.get(f"/api/chat/conversations/{chat['id']}")).json()['data']['version'] == chat['version']

    async def unavailable(*args):
        raise httpx.ConnectError('unavailable')

    monkeypatch.setattr(intelligence, 'structured', unavailable)
    assert (await send(client, chat)).status_code == 503
    assert (await client.put('/api/chat/memory', json={"content": "", "expected_version": 1})).json()['data']['content'] == ''


async def test_overview_and_upload_validation(client):
    chat = await upload(client, await new_chat(client), "星河公司的密码轮换周期为九十天。")
    response = await send(client, chat, "总结这份文档")
    assert "九十天" in response.json()['data']['messages'][0]['answer']
    bad = await client.post(f"/api/chat/conversations/{chat['id']}/documents", params={"expected_version": chat['version']}, files={"file": ('bad.exe', b'bad')})
    assert bad.status_code == 415
    oversized = await client.post(f"/api/chat/conversations/{chat['id']}/documents", params={"expected_version": chat['version']}, files={"file": ('big.txt', b'x' * (2 * 1024 * 1024 + 1))})
    assert oversized.status_code == 413


async def test_knowledge_versions_and_cross_space_isolation(client):
    from tests.test_open_registration import anonymous, register

    for version, body in [(1, '星河产品上线验收必须导入八十名成员，并完成全部培训。'), (2, '星河产品上线验收必须导入九十名成员，并完成全部培训。')]:
        result = await client.post('/api/knowledge', json={"title": "星河上线手册", "version": version, "module": "上线", "source": "内部知识库", "license": "内部授权", "body": body})
        assert result.status_code == 200, result.text
    chat = await new_chat(client)
    response = await send(client, chat)
    saved = response.json()['data']
    assert '九十' in saved['messages'][-1]['answer']
    assert '八十' not in saved['messages'][-1]['answer']
    await client.post(f"/api/knowledge/{result.json()['data']['id']}/deactivate")
    assert all(c['kind'] == 'platform_manual' for c in (await send(client, saved)).json()['data']['messages'][-1]['citations'])
    await client.put('/api/chat/memory', json={"content": "公司私有记忆", "expected_version": 0})
    async with anonymous() as other:
        await register(other, 'chat-other@example.com')
        other.headers['X-CSRF-Token'] = other.cookies.get('saas_csrf')
        assert (await other.get(f"/api/chat/conversations/{chat['id']}")).status_code == 404
        assert (await other.get('/api/chat/memory')).json()['data']['content'] == ''
        other_chat = await new_chat(other)
        assert all(c['kind'] == 'platform_manual' for c in (await send(other, other_chat)).json()['data']['messages'][-1]['citations'])


async def test_general_llm_and_memory_opt_out(client, monkeypatch):
    await client.put('/api/chat/memory', json={"content": "保存的偏好", "expected_version": 0})
    observed = []

    async def fake(task, source, schema):
        observed.append(source)
        return ChatAnswer(answer='你好，我可以帮你分析问题。', citation_ids=[])

    monkeypatch.setattr(get_settings(), 'model_mode', 'real')
    monkeypatch.setattr(intelligence, 'structured', fake)
    chat = await new_chat(client)
    response = await send(client, chat, '你好', use_memory=False)
    assert response.status_code == 200
    assert observed[-1]['memory'] == ''
    assert observed[-1]['evidence'] == []
    chat = await new_chat(client)
    assert (await send(client, chat, '你好')).status_code == 200
    assert observed[-1]['memory'] == '保存的偏好'


def test_docx_parser_and_context_does_not_restore_deleted_evidence():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>项目交付说明</w:t></w:r></w:p></w:body></w:document>')
    assert extract_document('test.docx', buffer.getvalue()) == '项目交付说明'
    with pytest.raises(HTTPException):
        extract_document('bad.docx', b'not a zip')
    message = {"question": "要求是什么", "answer": "旧秘密材料", "citations": [{"id": "removed", "text": "旧秘密材料"}]}
    assert '旧秘密材料' not in str(conversation_context([message], '继续', []))


def test_pdf_text_and_scanned_pdf_rejection():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=400)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 20 300 Td (Release requires 80 members.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    assert '80 members' in extract_document('guide.pdf', buffer.getvalue())
    blank = PdfWriter()
    blank.add_blank_page(width=400, height=400)
    buffer = io.BytesIO()
    blank.write(buffer)
    with pytest.raises(HTTPException):
        extract_document('scan.pdf', buffer.getvalue())
