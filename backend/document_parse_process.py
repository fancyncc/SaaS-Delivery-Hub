"""Killable parser process for untrusted binary documents."""

import importlib
import json
import subprocess
import sys
from pathlib import Path

from fastapi import HTTPException

from backend.document_parser import Node, Parsed, parse


def parse_isolated(filename: str, raw: bytes, *, csv_header: bool = True) -> Parsed:
    timeout = 600 if filename.lower().endswith(".pdf") else 120
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "backend.document_parse_process", filename, str(int(csv_header))],
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
        resource = importlib.import_module("resource")
        limit = 768 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except ImportError:
        # Windows has no resource module. A Job Object enforces committed memory.
        windows_memory_limit()
    try:
        result = parse(sys.argv[1], sys.stdin.buffer.read(), csv_header=bool(int(sys.argv[2])))
        payload = result.dump()
    except HTTPException as exc:
        payload = {"status": exc.status_code, "error": str(exc.detail)}
    # The parent reads JSON bytes as UTF-8, regardless of Windows console locale.
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def windows_memory_limit():
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                    ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io", ctypes.c_uint64 * 6),
                    ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    kernel = importlib.import_module("ctypes").WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    job = kernel.CreateJobObjectW(None, None)
    info = ExtendedLimits()
    info.basic.flags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    info.process_memory = 768 * 1024 * 1024
    if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        raise RuntimeError("Unable to enforce parser memory limit")
    # The OS closes the handle on child exit; keep it open for the entire parse.


if __name__ == "__main__":
    main()
