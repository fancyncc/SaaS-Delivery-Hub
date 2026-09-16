from tests.test_api import project, role_client
from tests.test_chat import new_chat, send, upload


async def test_pin_and_unread_persist_and_check_version(client):
    chat = await new_chat(client)
    path = f"/api/chat/conversations/{chat['id']}"
    response = await client.patch(path, json={'expected_version': chat['version'], 'pinned': True, 'unread': True})
    assert response.status_code == 200
    updated = response.json()['data']
    assert updated['pinned'] and updated['unread']
    saved = (await client.get(path)).json()['data']
    assert saved['pinned'] and saved['unread']
    rows = (await client.get('/api/chat/conversations')).json()['data']['conversations']
    assert next(row for row in rows if row['id'] == chat['id'])['pinned']
    stale = await client.patch(path, json={'expected_version': chat['version'], 'unread': False})
    assert stale.status_code == 409
    cleared = await client.patch(path, json={'expected_version': updated['version'], 'unread': False, 'pinned': False})
    assert cleared.status_code == 200
    assert not cleared.json()['data']['unread']
    assert not cleared.json()['data']['pinned']


async def test_rename_archive_restore_and_delete(client):
    chat = await upload(client, await new_chat(client))
    changed = await client.patch(f"/api/chat/conversations/{chat['id']}", json={'expected_version': chat['version'], 'title': '  验收讨论  '})
    assert changed.status_code == 200
    chat = changed.json()['data']
    assert chat['title'] == '验收讨论'
    chat = (await send(client, chat)).json()['data']
    assert chat['title'] == '验收讨论'
    for title in ('   ', ''):
        result = await client.patch(f"/api/chat/conversations/{chat['id']}", json={'expected_version': chat['version'], 'title': title})
        assert result.status_code == 422
    result = await client.patch(f"/api/chat/conversations/{chat['id']}", json={'expected_version': 1, 'archived': True})
    assert result.status_code == 409
    result = await client.patch(f"/api/chat/conversations/{chat['id']}", json={'expected_version': chat['version'], 'archived': True})
    assert result.status_code == 200
    archived = result.json()['data']
    assert archived['archived'] is True and archived['messages'] == chat['messages']
    assert (await client.get('/api/chat/conversations')).json()['data']['conversations'] == []
    rows = (await client.get('/api/chat/conversations?archived=true&q=验收')).json()['data']['conversations']
    assert [r['id'] for r in rows] == [chat['id']]
    assert (await client.get(f"/api/chat/conversations/{chat['id']}")).json()['data']['archived']
    assert (await send(client, archived)).status_code == 409
    result = await client.post(f"/api/chat/conversations/{chat['id']}/documents", params={'expected_version': archived['version']}, files={'file': ('more.txt', b'more content')})
    assert result.status_code == 409
    result = await client.patch(f"/api/chat/conversations/{chat['id']}", json={'expected_version': archived['version'], 'archived': False})
    restored = result.json()['data']
    assert (await send(client, restored)).status_code == 200
    assert (await client.delete(f"/api/chat/conversations/{chat['id']}")).status_code == 200
    assert (await client.get(f"/api/chat/conversations/{chat['id']}")).status_code == 404


async def test_management_is_private_and_archived_deletion(client):
    p = await project(client)
    chat = await new_chat(client, p['id'])
    member = await role_client(client, p['id'], 'viewer', 'manage-chat@example.com')
    try:
        assert (await member.patch(f"/api/chat/conversations/{chat['id']}", json={'expected_version': 1, 'title': 'other'})).status_code == 404
        assert (await member.delete(f"/api/chat/conversations/{chat['id']}")).status_code == 404
        assert (await member.get('/api/chat/conversations?archived=true')).json()['data']['conversations'] == []
    finally:
        await member.aclose()
    assert (await client.patch(f"/api/chat/conversations/{chat['id']}", json={'expected_version': 1, 'archived': True})).status_code == 200
    assert (await client.delete(f"/api/chat/conversations/{chat['id']}")).status_code == 200
