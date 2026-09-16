import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from test_api import project


@pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser acceptance")
async def test_agent_progress_and_resume_in_browser(client, monkeypatch):
    from playwright.async_api import async_playwright, expect

    from backend.config import get_settings

    monkeypatch.setattr(get_settings(), "agent_engine", "v2")
    monkeypatch.setattr(get_settings(), "agent_max_rounds", 1)
    p = await project(client)
    started = (await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid.uuid4())})).json()["data"]
    assert started["status"] == "blocked"
    base = "http://127.0.0.1:18113"
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "browser_host:app", "--app-dir", "tests", "--host", "127.0.0.1", "--port", "18113"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "AGENT_MAX_ROUNDS": "20"})
    try:
        async with httpx.AsyncClient(timeout=1, trust_env=False) as probe:
            for _ in range(60):
                try:
                    if (await probe.get(base + "/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(.25)
            else:
                pytest.fail("browser host unavailable")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel=os.getenv("PLAYWRIGHT_CHANNEL") or None)
            page = await browser.new_page(viewport={"width": 1400, "height": 1000})
            await page.goto(base + "/login")
            await page.get_by_label("账号", exact=True).fill("company-admin@example.com")
            await page.get_by_label("密码", exact=True).fill("CompanyAdmin123")
            await page.get_by_role("button", name="登录", exact=True).click()
            await page.wait_for_url(base + "/app")
            await page.goto(base + f"/app/runs/{started['id']}")
            panel = page.locator(".agent-progress")
            await expect(panel).to_be_visible()
            await page.get_by_label("处理说明").fill("已确认预算，恢复剩余步骤")
            await page.get_by_role("button", name="从当前进度恢复").click()
            await expect(page.get_by_role("button", name="从当前进度恢复")).to_have_count(0, timeout=20000)
            await expect(panel.get_by_text("✓ 已完成 · 生成实施计划", exact=True)).to_be_visible()
            artifacts = Path("tests/artifacts")
            artifacts.mkdir(exist_ok=True)
            await panel.screenshot(path=str(artifacts / "agent-progress.png"))
            await page.set_viewport_size({"width": 390, "height": 844})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            await browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
