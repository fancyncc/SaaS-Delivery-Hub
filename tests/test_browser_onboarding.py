import asyncio
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest


@pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1", reason="Requires built web and Chromium")
async def test_browser_register_create_company_and_import():
    from playwright.async_api import async_playwright, expect

    base = "http://127.0.0.1:18112"
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "browser_host:app", "--app-dir", "tests",
                                "--host", "127.0.0.1", "--port", "18112"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=1) as probe:
            for _ in range(80):
                try:
                    if (await probe.get(base + "/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
            else:
                pytest.fail("Browser API failed to start")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel=os.getenv("PLAYWRIGHT_CHANNEL") or None)
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 1000})
                await page.goto(base + "/login")
                await page.get_by_role("link", name="创建账号", exact=True).click()
                artifact_dir = Path("tests/artifacts")
                artifact_dir.mkdir(exist_ok=True)
                await page.get_by_label("账号", exact=True).fill("browser-open")
                await page.get_by_label("密码", exact=True).fill("BrowserPass123")
                await page.get_by_label("确认密码", exact=True).fill("BrowserPass123")
                await page.screenshot(path=str(artifact_dir / "account-registration.png"), full_page=True)
                await page.get_by_role("button", name="创建账号", exact=True).click()
                await page.wait_for_url(base + "/app/profile")
                await page.get_by_label("邮箱", exact=True).fill("browser-open@example.com")
                await page.get_by_label("邮箱绑定确认密码", exact=True).fill("BrowserPass123")
                await page.get_by_role("button", name="发送绑定邮件", exact=True).click()
                link = page.get_by_role("link", name="开发模式：打开验证链接", exact=False)
                await expect(link).to_be_visible()
                target = urlsplit(await link.get_attribute("href"))
                await page.goto(base + target.path + "?" + target.query)
                await page.get_by_role("button", name="确认绑定邮箱", exact=True).click()
                await page.wait_for_url(base + "/app/profile")
                await page.get_by_label("手机号", exact=True).fill("13800138000")
                await page.get_by_label("手机号保存确认密码", exact=True).fill("BrowserPass123")
                await page.get_by_role("button", name="保存手机号", exact=True).click()
                await expect(page.get_by_text("已填写 · 未验证", exact=True)).to_be_visible()
                assert await page.locator('a').evaluate_all("els => els.every(e => getComputedStyle(e).color !== 'rgb(0, 0, 238)' && getComputedStyle(e).textDecorationLine === 'none')")
                await page.screenshot(path=str(artifact_dir / "account-profile.png"), full_page=True)
                await page.get_by_role("link", name="空间与账号", exact=True).click()
                await page.get_by_label("公司名称", exact=True).fill("浏览器开放公司")
                await page.get_by_label("空间标识", exact=False).fill("browser-open-company")
                await page.get_by_role("button", name="创建公司", exact=True).click()
                await page.wait_for_url(base + "/app")
                await page.get_by_role("link", name="公司设置", exact=True).click()
                await expect(page.locator('.metric').first).to_be_visible()
                await page.screenshot(path=str(artifact_dir / "company-settings-refined.png"), full_page=True)
                await page.get_by_role("link", name="待办与知识库", exact=True).click()
                await expect(page.get_by_role("button", name="发布知识版本", exact=True)).to_be_visible()
                workbench_url = page.url
                await page.goto(base + "/app/rag-check")
                await expect(page.get_by_role("heading", name="RAG 查验", exact=True)).to_be_visible()
                await expect(page.get_by_label("查验流程", exact=True)).to_have_count(0)
                await expect(page.get_by_label("精选证据预算", exact=True)).to_be_visible()
                await expect(page.get_by_text("旧版流程（兼容）", exact=False)).to_have_count(0)
                assert "V3" not in await page.locator('label[for="evidence-budget"]').inner_text()
                await page.goto(workbench_url)
                await page.screenshot(path=str(artifact_dir / "workbench-refined.png"), full_page=True)
                await page.set_viewport_size({"width": 390, "height": 844})
                assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                await page.screenshot(path=str(artifact_dir / "workbench-mobile.png"), full_page=True)
                await page.set_viewport_size({"width": 1440, "height": 1000})
                await page.get_by_role("link", name="公司设置", exact=True).click()
                await page.get_by_role("button", name="成员与邀请", exact=True).click()
                await page.get_by_role("link", name="批量导入公司成员", exact=True).click()
                await page.get_by_label("选择 CSV 文件").set_input_files({"name": "members.csv", "mimeType": "text/csv",
                    "buffer": "姓名,邮箱,部门,工号\n测试成员,browser-employee@example.com,实施部,001".encode()})
                await page.get_by_role("button", name="校验文件", exact=True).click()
                await expect(page.get_by_text("可新增 1 条", exact=False)).to_be_visible()
                await page.get_by_role("button", name="确认提交并发送激活邮件", exact=True).click()
                await expect(page.get_by_text("已提交 1 名待激活成员", exact=False)).to_be_visible()
                artifact_dir = Path("tests/artifacts")
                artifact_dir.mkdir(exist_ok=True)
                await page.screenshot(path=str(artifact_dir / "open-registration-import.png"), full_page=True)
                await page.get_by_role("link", name="空间与账号", exact=True).click()
                await expect(page.get_by_text("browser-open的个人空间", exact=False)).to_be_visible()
                await page.get_by_role("button", name="切换到此空间", exact=True).click()
                await page.wait_for_url(base + "/app")
                await expect(page.get_by_role("link", name="公司设置", exact=True)).to_have_count(0)
                await page.get_by_role("link", name="空间与账号", exact=True).click()
                await page.screenshot(path=str(artifact_dir / "open-registration-spaces.png"), full_page=True)
            finally:
                await browser.close()
    finally:
        process.terminate()
        await asyncio.to_thread(process.wait, timeout=10)
