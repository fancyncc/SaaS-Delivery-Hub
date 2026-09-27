import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx
import pytest


@pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser acceptance")
async def test_chat_upload_memory_and_reload(client):
    from playwright.async_api import async_playwright, expect

    from tests.test_api import project

    scoped_project = await project(client)

    base = "http://127.0.0.1:18114"
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "browser_host:app", "--app-dir", "tests", "--host", "127.0.0.1", "--port", "18114"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "MODEL_MODE": "deterministic", "EMBEDDING_MODEL": "",
             "CHAT_CONTEXT_MODE": "on", "CHAT_HISTORY_ENABLED": "true",
             "CHAT_MEMORY_ITEMS_ENABLED": "true", "CHAT_MEMORY_CANDIDATES_ENABLED": "true"})
    try:
        async with httpx.AsyncClient(timeout=1, trust_env=False) as probe:
            for _ in range(60):
                try:
                    if (await probe.get(base + '/health')).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(.25)
            else:
                pytest.fail('browser host unavailable')
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel=os.getenv('PLAYWRIGHT_CHANNEL') or None)
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto(base + '/login')
            await page.get_by_label('账号', exact=True).fill('company-admin@example.com')
            await page.get_by_label('密码', exact=True).fill('CompanyAdmin123')
            await page.get_by_role('button', name='登录', exact=True).click()
            await page.wait_for_url(base + '/app')
            await page.get_by_role('link', name='AI 助手', exact=True).click()
            await expect(page.get_by_role('heading', name='新对话', exact=True)).to_be_visible()
            await expect(page.locator('input[type=file]')).to_be_enabled()
            await page.locator('input[type=file]').set_input_files({"name": "上线要求.md", "mimeType": "text/markdown", "buffer": "上线验收必须完成 80 名成员导入和管理员培训。".encode()})
            await expect(page.get_by_text('文档已解析，可以开始提问。')).to_be_visible()
            await expect(page.get_by_role('combobox', name='检索范围', exact=True)).to_be_disabled()
            await page.get_by_label('输入问题', exact=True).fill('上线验收要求是什么？')
            await page.get_by_role('button', name='发送消息', exact=True).click()
            await expect(page.locator('.result-answer .answer-text')).to_contain_text('80')
            await page.locator('.source-card summary').first.click()
            await expect(page.locator('.source-card').first).to_contain_text('上线要求.md')
            await page.get_by_role('button', name='☷ 管理长期记忆', exact=True).click()
            await page.get_by_role('textbox', name='长期记忆', exact=True).fill('请用中文，先给结论。')
            await page.get_by_role('button', name='保存原有记忆', exact=True).click()
            await expect(page.get_by_role('status').filter(has_text='偏好已保存')).to_be_visible()
            await expect(page.get_by_role('checkbox', name='从明确的个人陈述生成待确认候选')).not_to_be_checked()
            await page.get_by_role('combobox', name=re.compile('^适用范围')).select_option('conversation')
            await page.get_by_label('主题', exact=True).fill('回复格式')
            await page.get_by_label('内容', exact=True).fill('将上线要求列为清单。')
            await page.get_by_role('button', name='确认保存条目', exact=True).click()
            await expect(page.get_by_role('textbox', name='回复格式的内容', exact=True)).to_have_value('将上线要求列为清单。')
            await page.get_by_role('button', name='关闭长期记忆', exact=True).click()
            saved_url = page.url
            await page.reload()
            await expect(page.locator('.result-answer .answer-text')).to_contain_text('80')
            assert page.url == saved_url
            await page.get_by_role('button', name='☷ 管理长期记忆', exact=True).click()
            await expect(page.get_by_role('textbox', name='回复格式的内容', exact=True)).to_have_value('将上线要求列为清单。')
            await expect(page.get_by_role('checkbox', name='从明确的个人陈述生成待确认候选')).not_to_be_checked()
            await page.get_by_role('button', name='关闭长期记忆', exact=True).click()
            await page.get_by_label('搜索历史对话', exact=True).fill('80')
            await expect(page.locator('.history-item')).to_have_count(1)
            await page.get_by_label('搜索历史对话', exact=True).fill('找不到的对话标记')
            await expect(page.locator('.history-item')).to_have_count(0)
            await page.get_by_label('搜索历史对话', exact=True).fill('')
            await expect(page.locator('.history-item')).to_have_count(1)
            await page.locator('.history-item').first.click()
            await expect(page.locator('.result-answer .answer-text')).to_contain_text('80')
            artifacts = Path('tests/artifacts')
            artifacts.mkdir(exist_ok=True)
            await page.screenshot(path=str(artifacts / 'chat-desktop.png'), full_page=True)
            await page.set_viewport_size({"width": 390, "height": 844})
            await page.screenshot(path=str(artifacts / 'chat-mobile.png'), full_page=True)
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            await page.get_by_role('button', name='打开历史对话', exact=True).click()
            await expect(page.locator('.history-item').first).to_be_visible()
            await page.screenshot(path=str(artifacts / 'chat-mobile-history.png'), full_page=True)
            await page.get_by_role('button', name='＋ 新建对话', exact=True).click()
            await expect(page.get_by_role('combobox', name='检索范围', exact=True)).to_be_enabled()
            await page.get_by_role('combobox', name='检索范围', exact=True).select_option(scoped_project['id'])
            await expect(page.get_by_role('combobox', name='检索范围', exact=True)).to_have_value(scoped_project['id'])
            await page.get_by_label('输入问题', exact=True).fill('我的项目当前状态是什么？')
            await page.get_by_role('button', name='发送消息', exact=True).click()
            await expect(page.locator('.result-answer .answer-text')).to_contain_text('草稿')
            await expect(page.locator('.result-answer .answer-text')).not_to_contain_text('project_status')
            await expect(page.get_by_role('combobox', name='检索范围', exact=True)).to_be_disabled()
            await page.get_by_role('button', name='打开历史对话', exact=True).click()
            await page.locator('.history-item').filter(has_text='上线验收要求是什么').click()
            await page.get_by_label('输入问题', exact=True).fill('那管理员培训呢？')
            await page.get_by_role('button', name='发送消息', exact=True).click()
            await expect(page.locator('.chat-turn')).to_have_count(2)
            await expect(page.locator('.result-answer')).to_have_count(2)
            await page.set_viewport_size({'width': 1000, 'height': 600})
            await page.screenshot(path=str(artifacts / 'chat-compact.png'), full_page=True)
            assert await page.locator('.history-item').first.evaluate('(el) => el.getBoundingClientRect().height >= 44')
            assert await page.evaluate('document.documentElement.scrollHeight <= innerHeight && document.documentElement.scrollWidth <= innerWidth')
            await page.get_by_role('button', name='管理对话：上线验收要求是什么？', exact=True).click()
            await page.get_by_role('button', name='✎ 重命名', exact=True).click()
            await page.get_by_label('对话名称', exact=True).fill('迁移验收讨论')
            await page.get_by_role('button', name='保存名称', exact=True).click()
            await expect(page.get_by_role('heading', level=1)).to_contain_text('迁移验收讨论')
            await page.get_by_role('button', name='管理对话：迁移验收讨论', exact=True).click()
            await page.get_by_role('button', name='▤ 归档对话', exact=True).click()
            await expect(page.get_by_text('此对话已归档。', exact=False)).to_be_visible()
            await expect(page.get_by_label('输入问题', exact=True)).to_be_disabled()
            await page.get_by_role('button', name='已归档', exact=True).click()
            await expect(page.locator('.history-item')).to_have_count(1)
            await page.reload()
            await expect(page.get_by_text('此对话已归档。', exact=False)).to_be_visible()
            await page.get_by_role('button', name='恢复对话', exact=True).click()
            await expect(page.get_by_label('输入问题', exact=True)).to_be_enabled()
            await page.get_by_role('button', name='对话', exact=True).click()
            await page.get_by_role('button', name='管理对话：迁移验收讨论', exact=True).click()
            await page.locator('dialog[open]').get_by_role('button', name='删除对话', exact=True).click()
            await page.get_by_role('button', name='取消', exact=True).click()
            await expect(page.locator('.chat-turn')).to_have_count(2)
            await page.get_by_role('button', name='管理对话：迁移验收讨论', exact=True).click()
            await page.locator('dialog[open]').get_by_role('button', name='删除对话', exact=True).click()
            await page.get_by_role('button', name='确认删除', exact=True).click()
            await expect(page.locator('.chat-turn')).to_have_count(0)
            await expect(page.locator('.history-item').filter(has_text='迁移验收讨论')).to_have_count(0)
            await page.get_by_role('link', name='创建项目 ↗', exact=True).click()
            await expect(page.get_by_role('heading', name='新建实施项目', exact=True)).to_be_visible()
            await page.get_by_placeholder('如：星河设计客户上线实施').fill('从助手创建的验收项目')
            await page.get_by_placeholder('项目主要协调人').fill('测试联系人')
            await page.get_by_placeholder('name@company.com').fill('chat-project@example.com')
            await page.locator('input[type=date]').fill('2026-12-01')
            await page.get_by_placeholder('设计部、市场部、财务部').fill('研发部')
            await page.get_by_placeholder('请写清楚谁在什么场景下', exact=False).fill('研发部管理员需要导入成员并核对权限，完成培训后提交人工验收。')
            await page.get_by_role('button', name='创建实施项目', exact=True).click()
            await page.wait_for_url('**/app/chat?project=*')
            await expect(page.get_by_role('combobox', name='检索范围', exact=True).locator('option:checked')).to_have_text('从助手创建的验收项目')
            assert errors == []
            await browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
