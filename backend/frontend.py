"""Serve the compiled interface from the API origin."""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles


def mount_frontend(app: FastAPI) -> None:
    dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="frontend-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str):
        pages = {"", "login", "register", "verify-email", "accept-invitation",
                 "accept-platform-invitation", "forgot-password", "reset-password"}
        if path not in pages and path.split("/", 1)[0] not in {"app", "platform"}:
            raise HTTPException(404, "Not Found")
        if not (dist / "index.html").is_file():
            return HTMLResponse(
                '<html lang="zh-CN"><meta charset="utf-8"><title>SaaS Delivery Hub</title>'
                '<body style="font-family:sans-serif;max-width:640px;margin:80px auto;padding:24px">'
                '<h1>SaaS Delivery Hub</h1><p>后端已启动，应用页面尚未构建。</p>'
                '<p>请在项目根目录运行 <code>.\\start.ps1</code>，自动准备并启动应用。</p>'
                '<a href="/docs">查看接口文档</a></body></html>', status_code=503,
            )
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})
