"""Versioned contracts at the planner, generator and evaluator trust boundaries."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Criterion(StrictModel):
    kind: Literal["tool_completed"] = "tool_completed"
    tool: str
    required: Literal[True] = True


class Milestone(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    title: str = Field(min_length=1, max_length=200)
    tool: str
    dependencies: list[str] = Field(default_factory=list, max_length=30)
    criteria: list[Criterion] = Field(min_length=1, max_length=10)


class StagePlan(StrictModel):
    milestones: list[Milestone] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def dag(self):
        nodes = {m.id: m for m in self.milestones}
        if len(nodes) != len(self.milestones):
            raise ValueError("duplicate milestone ID")
        visited: set[str] = set()
        active: set[str] = set()

        def visit(identifier: str):
            if identifier not in nodes or identifier in active:
                raise ValueError("unknown dependency or cyclic DAG")
            if identifier in visited:
                return
            active.add(identifier)
            for dep in nodes[identifier].dependencies:
                visit(dep)
            active.remove(identifier)
            visited.add(identifier)
        for identifier in nodes:
            visit(identifier)
        return self


class ToolArguments(StrictModel):
    query: str | None = Field(default=None, min_length=1, max_length=2000)


class ToolAction(StrictModel):
    tool: str
    arguments: ToolArguments = Field(default_factory=ToolArguments)


class ActionBatch(StrictModel):
    actions: list[ToolAction] = Field(min_length=1, max_length=3)


class ToolObservation(StrictModel):
    action_id: str
    tool: str
    status: Literal["succeeded", "failed", "paused"]
    result: dict[str, Any] = Field(default_factory=dict)


class EvaluationResult(StrictModel):
    outcome: Literal["pass", "retry", "replan", "blocked"]
    reason: str
    milestone_id: str


class MemoryEntry(StrictModel):
    id: str
    advice: str
    source_run_id: str
    source_action_id: str
    verified: bool


class AgentState(StrictModel):
    stage: str = ""
    plan_version: int = 0
    plan: StagePlan | None = None
    completed: list[str] = Field(default_factory=list)
    rounds: int = 0
    retries: dict[str, int] = Field(default_factory=dict)
    replans: int = 0
    tokens_reserved: int = 0
    observations: list[ToolObservation] = Field(default_factory=list)
    evaluation: EvaluationResult | None = None
    knowledge: list[dict[str, Any]] = Field(default_factory=list)
    memories: list[MemoryEntry] = Field(default_factory=list)
    resume_count: int = 0
