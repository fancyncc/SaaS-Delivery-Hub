from __future__ import annotations

from enum import StrEnum

from backend.models import AgentRun, Project


class InvalidStateTransition(ValueError):
    pass


class ProjectLifecycle(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class RunLifecycle(StrEnum):
    BLOCKED = "blocked"
    PREPARING_MATERIALS = "preparing_materials"
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


PROJECT_TRANSITIONS: dict[str, frozenset[str]] = {
    ProjectLifecycle.DRAFT: frozenset({ProjectLifecycle.READY, ProjectLifecycle.IN_PROGRESS, ProjectLifecycle.CANCELLED}),
    ProjectLifecycle.READY: frozenset({ProjectLifecycle.IN_PROGRESS, ProjectLifecycle.CANCELLED}),
    ProjectLifecycle.IN_PROGRESS: frozenset({ProjectLifecycle.BLOCKED, ProjectLifecycle.COMPLETED, ProjectLifecycle.CANCELLED}),
    ProjectLifecycle.BLOCKED: frozenset({ProjectLifecycle.IN_PROGRESS, ProjectLifecycle.CANCELLED}),
    ProjectLifecycle.COMPLETED: frozenset({ProjectLifecycle.ARCHIVED}),
    ProjectLifecycle.CANCELLED: frozenset({ProjectLifecycle.ARCHIVED}),
    ProjectLifecycle.ARCHIVED: frozenset(),
}

RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    RunLifecycle.BLOCKED: frozenset({RunLifecycle.RUNNING, RunLifecycle.CANCELLED}),
    RunLifecycle.PENDING: frozenset({RunLifecycle.RUNNING, RunLifecycle.CANCELLED}),
    RunLifecycle.RUNNING: frozenset({RunLifecycle.BLOCKED, RunLifecycle.PREPARING_MATERIALS, RunLifecycle.WAITING_APPROVAL, RunLifecycle.SUCCEEDED, RunLifecycle.FAILED, RunLifecycle.CANCELLED}),
    RunLifecycle.PREPARING_MATERIALS: frozenset({RunLifecycle.RUNNING, RunLifecycle.FAILED, RunLifecycle.CANCELLED}),
    RunLifecycle.WAITING_APPROVAL: frozenset({RunLifecycle.RUNNING, RunLifecycle.FAILED, RunLifecycle.CANCELLED}),
    RunLifecycle.SUCCEEDED: frozenset(),
    RunLifecycle.FAILED: frozenset(),
    RunLifecycle.CANCELLED: frozenset(),
}


def _transition(current: str, target: str, transitions: dict[str, frozenset[str]], aggregate: str) -> None:
    if current == target:
        return
    if target not in transitions.get(current, frozenset()):
        raise InvalidStateTransition(f"{aggregate} cannot transition from {current} to {target}")


def transition_project(project: Project, target: ProjectLifecycle | str) -> None:
    target_value = str(target)
    _transition(project.lifecycle_status, target_value, PROJECT_TRANSITIONS, "Project")
    if project.lifecycle_status != target_value:
        project.lifecycle_status = target_value
        project.version += 1


def transition_run(run: AgentRun, target: RunLifecycle | str) -> None:
    target_value = str(target)
    _transition(run.status, target_value, RUN_TRANSITIONS, "Run")
    if run.status != target_value:
        run.status = target_value
        run.version += 1
