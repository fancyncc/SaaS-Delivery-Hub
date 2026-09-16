"""Read-only visual checks against the local demo workspace."""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright, expect


async def main():
    output = Path(__file__).resolve().parents[1] / 'data/demo_workspace'
    account = json.loads((output / 'credentials.json').read_text(encoding='utf-8'))['admin']
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel='chrome')
        page = await browser.new_page(viewport={'width': 1440, 'height': 960})
        response = await page.request.post('http://127.0.0.1:8000/api/auth/login', data={'username': account['username'], 'password': account['password']})
        assert response.ok
        await page.goto('http://127.0.0.1:8000/app/chat')
        await expect(page.locator('.history-item').first).to_be_visible()
        await page.screenshot(path=str(output / 'chat-redesign-empty.png'))
        await page.locator('.history-menu-button').first.click()
        await expect(page.get_by_role('heading', name='管理对话', exact=True)).to_be_visible()
        await page.screenshot(path=str(output / 'chat-management-menu.png'))
        await page.get_by_role('button', name='关闭对话管理', exact=True).click()
        await page.locator('.history-item').filter(has_text='XL-104').first.click()
        await expect(page.locator('.chat-turn').first).to_be_visible()
        await page.screenshot(path=str(output / 'chat-redesign-history.png'))
        saved_url = page.url
        await page.reload()
        await expect(page.locator('.chat-turn').first).to_be_visible()
        assert saved_url == page.url
        for width, height, name in [(1000, 600, 'compact'), (390, 844, 'mobile')]:
            await page.set_viewport_size({'width': width, 'height': height})
            await page.screenshot(path=str(output / f'chat-redesign-{name}.png'))
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight')
        await page.get_by_role('button', name='打开历史对话', exact=True).click()
        await expect(page.locator('.history-item').first).to_be_visible()
        await expect(page.locator('.chat-sidebar')).to_have_css('transform', 'matrix(1, 0, 0, 1, 0, 0)')
        await page.screenshot(path=str(output / 'chat-redesign-mobile-history.png'))
        assert await page.locator('.history-item').first.evaluate('(el) => el.getBoundingClientRect().height >= 60')
        print('Verified existing history, reload, desktop, compact, mobile and history drawer.')
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
