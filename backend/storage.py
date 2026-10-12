from __future__ import annotations

import asyncio

import boto3
from fastapi import HTTPException

from backend.config import get_settings


def client():
    settings = get_settings()
    return boto3.client("s3", endpoint_url=settings.s3_endpoint, aws_access_key_id=settings.s3_access_key, aws_secret_access_key=settings.s3_secret_key)


def key(item) -> str:
    return f"{item.tenant_id}/{item.project_id}/{item.checksum}"


async def persist(item) -> None:
    if get_settings().storage_backend == "s3":
        from backend.observability import span
        with span("storage.put"):
            await asyncio.to_thread(client().put_object, Bucket=get_settings().s3_bucket, Key=key(item), Body=item.content.encode(), ContentType="text/markdown; charset=utf-8")


async def read(item) -> str:
    from backend.delivery import digest
    if get_settings().storage_backend != "s3":
        return item.content
    try:
        from backend.observability import span
        with span("storage.get"):
            response = await asyncio.to_thread(client().get_object, Bucket=get_settings().s3_bucket, Key=key(item))
            value = (await asyncio.to_thread(response["Body"].read)).decode()
        if digest(value) != item.checksum:
            raise ValueError("checksum mismatch")
        return value
    except Exception:
        raise HTTPException(503, "交付物存储不可用或校验失败") from None
