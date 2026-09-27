import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from test_api import project, role_client


@pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1", reason="Set RUN_BROWSER_TESTS=1 after building web and installing Chromium")
async def test_browser_80_member_delivery(client):
    from playwright.async_api import async_playwright, expect

    p = await project(client)
    approver_api = await role_client(client, p["id"], "approver", "browser-approver@example.com")
    consultant_api = await role_client(client, p["id"], "implementation_consultant", "browser-consultant@example.com")
    base = "http://127.0.0.1:18111"
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "browser_host:app", "--app-dir", "tests", "--host", "127.0.0.1", "--port", "18111"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
                pytest.fail("E2E API failed to start")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel=os.getenv("PLAYWRIGHT_CHANNEL") or None)
            async def login(email, password, platform=False):
                context = await browser.new_context()
                page = await context.new_page()
                await page.goto(base + ("/platform/login" if platform else "/login"))
                await page.get_by_label("平台账号邮箱" if platform else "账号", exact=True).fill(email)
                await page.get_by_label("密码", exact=True).fill(password)
                await page.get_by_role("button", name="进入平台后台" if platform else "登录", exact=True).click()
                await page.wait_for_url(base + ("/platform" if platform else "/app"))
                return page
            manager = await login("company-admin@example.com", "CompanyAdmin123")
            # Collaboration lives in details; shared/reloaded URLs retain the tab.
            await expect(manager.get_by_role("button", name=re.compile("项目文档|协作任务"))).to_have_count(0)
            await manager.get_by_role("link", name="查看详情", exact=True).click()
            await expect(manager.locator(".project-detail-navigation")).to_be_visible()
            await manager.get_by_role("button", name=re.compile("项目文档")).click()
            await manager.wait_for_url("**tab=documents")
            documents_url = manager.url
            await manager.reload()
            await expect(manager.get_by_role("heading", name=re.compile("文档资料"))).to_be_visible()
            await manager.get_by_role("button", name=re.compile("协作任务")).click()
            await manager.wait_for_url("**tab=tasks")
            await manager.reload()
            await expect(manager.get_by_role("heading", name="协作任务", exact=True)).to_be_visible()
            await manager.goto(documents_url)
            await expect(manager.get_by_role("heading", name=re.compile("文档资料"))).to_be_visible()
            await manager.get_by_role("button", name="项目概览", exact=True).click()
            await expect(manager.get_by_role("heading", name="基本信息", exact=True)).to_be_visible()
            assert "tab" not in parse_qs(urlsplit(manager.url).query)
            await manager.goto(base + "/app")
            artifact_dir = Path("tests/artifacts")
            artifact_dir.mkdir(exist_ok=True)
            await manager.get_by_role("button", name="启动 Agent", exact=True).click()
            await manager.wait_for_url("**/app/runs/*")
            await expect(manager.get_by_text("尚未执行上线检查", exact=True)).to_be_visible()
            await manager.locator('.delivery-panel').screenshot(path=str(artifact_dir / 'delivery-empty.png'))
            run_url = manager.url
            await expect(manager.get_by_role("button", name="批准并继续")).to_have_count(0)
            approver = await login("browser-approver@example.com", "RoleMember123")
            await approver.goto(run_url)
            await approver.get_by_role("button", name="批准并继续", exact=True).click()
            await expect(approver.get_by_text("审批已通过，Agent 已继续执行。", exact=True)).to_be_visible()
            await approver.reload()
            await approver.get_by_role("button", name="批准并继续", exact=True).click()
            consultant = await login("browser-consultant@example.com", "RoleMember123")
            await consultant.goto(run_url)
            csv_text = "name,email,department,role\n" + "\n".join(f"成员{i},browser{i}@example.com,设计部,{'admin' if i == 0 else 'member'}" for i in range(80))
            await consultant.get_by_label("成员 CSV 内容").fill(csv_text)
            await consultant.get_by_role("button", name="校验并提交审批").click()
            await expect(consultant.get_by_label("成员 CSV 内容")).to_have_count(0)
            await expect(consultant.locator('.run-status strong')).to_have_text('数据导入审批', timeout=15000)
            await approver.reload()
            await approver.get_by_role("button", name="批准并继续", exact=True).click()
            await expect(approver.get_by_text("审批已通过，Agent 已继续执行。", exact=True)).to_be_visible()
            await approver.reload()
            await approver.get_by_role("button", name="批准并继续", exact=True).click()
            await expect(approver.get_by_text("执行成功", exact=True)).to_be_visible(timeout=15000)
            data = (await client.get(f"/api/projects/{p['id']}/delivery")).json()["data"]
            assert len(data["members"]) == 80
            assert len(data["artifacts"]) == 6
            await expect(approver.get_by_text("实施总结 v1", exact=False)).to_be_visible()
            await approver.set_viewport_size({"width": 1440, "height": 1000})
            await approver.locator('.delivery-panel').screenshot(path=str(artifact_dir / 'delivery-workspace.png'))
            await approver.get_by_role("button", name="成员与导入").click()
            await approver.get_by_label("搜索实际成员").fill("browser79@example.com")
            await expect(approver.locator('.member-table tbody tr')).to_have_count(1)
            await approver.get_by_role("button", name="配置方案").click()
            await expect(approver.get_by_role("columnheader", name="拟变更为")).to_be_visible()
            await approver.get_by_role("button", name="实施计划", exact=False).click()
            await expect(approver.get_by_role("heading", name="实施计划与甘特图")).to_be_visible()
            await approver.get_by_role("button", name="需求与差距").click()
            await approver.set_viewport_size({"width": 390, "height": 844})
            assert await approver.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            await approver.locator('.delivery-panel').screenshot(path=str(artifact_dir / 'delivery-mobile.png'))
            await approver.set_viewport_size({"width": 1440, "height": 1000})
            await approver.screenshot(path=str(artifact_dir / "delivery-completed.png"), full_page=True)
            async with approver.expect_download() as download:
                await approver.get_by_role("link", name="下载", exact=True).first.click()
            assert (await download.value).suggested_filename.endswith(".md")
            await approver.get_by_role("link", name="查看执行 Trace", exact=True).click()
            await expect(approver.get_by_role("heading", name="执行 Trace", exact=True)).to_be_visible()
            async with approver.expect_download() as trace_download:
                await approver.get_by_role("button", name="下载 Trace JSON").click()
            assert (await trace_download.value).suggested_filename.startswith("trace-")
            platform = await login("admin@example.com", "ChangeMe123!", platform=True)
            await platform.set_viewport_size({"width": 1440, "height": 1000})
            await platform.get_by_role("button", name="客户用户", exact=True).click()
            platform.on("dialog", lambda dialog: dialog.accept())
            await platform.get_by_role("button", name="撤销会话", exact=True).first.click()
            await expect(platform.get_by_role("status")).to_contain_text("会话已撤销")
            await platform.screenshot(path=str(artifact_dir / 'platform-governance.png'), full_page=True)
            await platform.set_viewport_size({"width": 390, "height": 844})
            assert await platform.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            await platform.screenshot(path=str(artifact_dir / 'platform-mobile.png'), full_page=True)
            await platform.get_by_role("button", name="关闭成功提示").click()
            await expect(platform.get_by_role("status")).to_have_count(0)
            await platform.get_by_role("link", name="评测历史与对比").click()
            await platform.get_by_role("button", name="运行离线评测", exact=True).click()
            await expect(platform.get_by_role("heading", name="评测详情", exact=True)).to_be_visible()
            async with platform.expect_download() as evaluation_download:
                await platform.get_by_role("link", name="下载 JSON", exact=True).first.click()
            assert (await evaluation_download.value).suggested_filename.startswith("evaluation-")
            await browser.close()
    finally:
        process.terminate()
        await asyncio.to_thread(process.wait, timeout=15)
        await approver_api.aclose()
        await consultant_api.aclose()
