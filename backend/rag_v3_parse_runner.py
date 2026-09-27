"""Killable parser process for untrusted binary documents."""

import json
import subprocess
import sys
from pathlib import Path

from fastapi import HTTPException

from backend.rag_v3_parse import Node, Parsed, parse


def parse_isolated(filename: str, raw: bytes, *, csv_header: bool = True) -> Parsed:
    timeout = 600 if filename.lower().endswith(".pdf") else 120
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "backend.rag_v3_parse_runner", filename, str(int(csv_header))],
            input=raw,
            capture_output=True,
            cwd=Path(__file__).resolve().parents[1],
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(422, "文档解析超时，请拆分后重试") from None
    if completed.returncode != 0:
        raise HTTPException(422, "文档解析进程失败，请检查文件或联系管理员")
    try:
        payload = json.loads(completed.stdout)
        if "error" in payload:
            raise HTTPException(payload["status"], payload["error"])
        return Parsed(payload["format"], [Node(**node) for node in payload["nodes"]],
                      payload["warnings"], payload.get("ocr_pages", 0))
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, "文档解析结果无效") from None


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(2)
    try:
        import resource

        limit = 768 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except ImportError:
        pass
    try:
        result = parse(sys.argv[1], sys.stdin.buffer.read(), csv_header=bool(int(sys.argv[2])))
        payload = result.dump()
    except HTTPException as exc:
        payload = {"status": exc.status_code, "error": str(exc.detail)}
    # The parent reads JSON bytes as UTF-8, regardless of Windows console locale.
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
