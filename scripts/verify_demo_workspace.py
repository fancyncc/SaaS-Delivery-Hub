"""Non-destructive browser smoke check against the populated local demo."""
import asyncio
import json
from pathlib import Path

import httpx
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "demo_workspace"
BASE = "http://127.0.0.1:8000"


async def main():
    accounts = json.loads((OUTPUT / "credentials.json").read_text(encoding="utf-8"))
    manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    async with httpx.AsyncClient(base_url=BASE, trust_env=False) as client:
        for _ in range(40):
            try:
                if (await client.get('/health')).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(.25)
        else:
            raise RuntimeError('Local server unavailable')
        for key, account in accounts.items():
            response = await client.post('/api/auth/login', json={"username": account['username'], "password": account['password']})
            assert response.status_code == 200, key
            csrf = client.cookies.get('saas_csrf', '')
            response = await client.post(f"/api/auth/spaces/{manifest['tenant_id']}/switch", headers={"X-CSRF-Token": csrf})
            assert response.status_code == 200, key
            projects = (await client.get('/api/projects')).json()['data']
            assert len(projects) == 6, key
            if key != 'admin':
                assert (await client.get('/api/chat/conversations/' + manifest['chats']['private'])).status_code == 404
            print(f"Verified login and project visibility: {key}", flush=True)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome")
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(BASE + '/login')
        await page.get_by_label('账号', exact=True).fill(accounts['admin']['username'])
        await page.get_by_label('密码', exact=True).fill(accounts['admin']['password'])
        await page.get_by_role('button', name='登录', exact=True).click()
        await page.wait_for_url(BASE + '/app')
        await expect(page.locator('body')).to_contain_text('XL-106', timeout=15000)
        await page.screenshot(path=str(OUTPUT / '演示工作台.png'), full_page=True)
        await page.get_by_role('link', name='AI 助手', exact=True).click()
        await expect(page.locator('.chat-history button')).to_have_count(5)
        await page.locator('.chat-history button').filter(has_text='XL-104').click()
        await expect(page.locator('.chat-answer')).to_have_count(2)
        await expect(page.locator('.chat-answer').first).to_contain_text('40')
        await page.locator('.chat-answer details summary').first.click()
        await page.screenshot(path=str(OUTPUT / 'AI问答示例.png'), full_page=True)
        await page.goto(BASE + '/app/runs/' + manifest['projects']['XL-106']['run_id'])
        await expect(page.locator('body')).to_contain_text('实施总结', timeout=15000)
        await page.screenshot(path=str(OUTPUT / '完成交付示例.png'), full_page=True)
        assert errors == [], errors
        await browser.close()
    print('Live browser verification passed', flush=True)


if __name__ == '__main__':
    asyncio.run(main())
