import asyncio

from test_open_registration import anonymous, commit, headers, token, validate

from tests.mail_helpers import mail_link


async def signup(person, username):
    response = await person.post('/api/auth/register', json={'username': username, 'password': 'PersonalPass123'})
    assert response.status_code == 200, response.text


async def test_username_registration_and_phone_profile():
    async with anonymous() as first, anonymous() as second:
        await signup(first, ' First.User ')
        await signup(second, 'second.user')
        profile = (await first.get('/api/auth/profile')).json()['data']
        assert profile['username'] == 'first.user' and profile['email'] is None
        assert (await second.post('/api/auth/register', json={'username': 'FIRST.USER', 'password': 'PersonalPass123'})).status_code == 409
        body = {'phone': '138 0013 8000', 'current_password': 'wrong'}
        assert (await first.patch('/api/auth/profile', headers=headers(first), json=body)).status_code == 403
        body['current_password'] = 'PersonalPass123'
        assert (await first.patch('/api/auth/profile', headers=headers(first), json=body)).status_code == 200
        profile = (await first.get('/api/auth/profile')).json()['data']
        assert profile['phone'] == '+8613800138000' and not profile['phone_verified']
        assert (await second.patch('/api/auth/profile', headers=headers(second), json=body)).status_code == 409
        assert (await first.patch('/api/auth/profile', headers=headers(first), json={'phone': 'invalid', 'current_password': 'PersonalPass123'})).status_code == 422
        assert (await first.patch('/api/auth/profile', headers=headers(first), json={'username': 'renamed', 'current_password': 'PersonalPass123'})).status_code == 200
        async with anonymous() as login:
            assert (await login.post('/api/auth/login', json={'username': 'renamed', 'password': 'PersonalPass123'})).status_code == 200
            assert (await login.post('/api/auth/login', json={'username': '+8613800138000', 'password': 'PersonalPass123'})).status_code == 401
        assert (await first.patch('/api/auth/profile', headers=headers(first), json={'phone': None, 'current_password': 'PersonalPass123'})).status_code == 200
        assert (await first.get('/api/auth/profile')).json()['data']['phone'] is None


async def test_email_binding_belongs_to_requesting_account():
    async with anonymous() as first, anonymous() as second:
        await signup(first, 'email.first')
        await signup(second, 'email.second')
        body = {'email': ' Contact@Example.com ', 'current_password': 'PersonalPass123'}
        sent = await first.post('/api/auth/profile/email', headers=headers(first), json=body)
        assert sent.status_code == 200, sent.text
        raw = token(await mail_link("email_verification"))
        assert (await first.get('/api/auth/profile')).json()['data']['email'] is None
        assert (await second.post('/api/auth/verify-email', json={'token': raw})).status_code == 403
        assert (await first.post('/api/auth/verify-email', json={'token': raw})).status_code == 200
        assert (await first.post('/api/auth/verify-email', json={'token': raw})).status_code == 410
        profile = (await first.get('/api/auth/profile')).json()['data']
        assert profile['email'] == 'contact@example.com' and profile['email_verified']
        assert (await second.post('/api/auth/profile/email', headers=headers(second), json=body)).status_code == 409


async def test_concurrent_username_registration():
    async with anonymous() as first, anonymous() as second:
        body = {'username': 'concurrent.user', 'password': 'PersonalPass123'}
        responses = await asyncio.gather(first.post('/api/auth/register', json=body), second.post('/api/auth/register', json=body))
        assert sorted(r.status_code for r in responses) == [200, 409]


async def test_username_only_account_accepts_company_invitation(client):
    async with anonymous() as person:
        await signup(person, 'invited.person')
        before = (await person.get('/api/auth/me')).json()['data']
        batch = await validate(client, '姓名,邮箱,部门,工号\n邀请成员,invited@example.com,实施部,009')
        result = await commit(client, batch)
        raw = token(result['invitations'][0]['invitation_url'])
        response = await person.post(f'/api/auth/invitations/{raw}/accept', headers=headers(person), json={})
        assert response.status_code == 200, response.text
        after = (await person.get('/api/auth/me')).json()['data']
        assert after['id'] == before['id'] and after['username'] == 'invited.person'
        assert after['email'] == 'invited@example.com' and after['email_verified']
        assert len(after['memberships']) == 2
        async with anonymous() as login:
            assert (await login.post('/api/auth/login', json={'username': 'invited.person', 'password': 'PersonalPass123'})).status_code == 200
