"""Validated optional tool inputs; actor and tenant identity are server-owned."""
from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyArguments(StrictArguments):
    pass


class PreviewArguments(StrictArguments):
    job_id: UUID | None = None


class SearchOfficialArguments(StrictArguments):
    product: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    topic: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    version: str = Field(default="", max_length=40)


class ReadOfficialArguments(StrictArguments):
    reference: str = Field(min_length=1, max_length=4096)


class ToolResult(BaseModel):
    call_id: str = Field(default_factory=lambda: str(uuid4()))
    status: Literal["succeeded", "unavailable"] = "succeeded"
    tool: str
    version: str = "1"
    result: dict = Field(default_factory=dict)
    sources: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
