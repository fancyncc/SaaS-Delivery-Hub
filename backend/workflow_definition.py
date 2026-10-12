"""Shared business order, stages, permissions and approval boundaries."""
from dataclasses import dataclass

from backend.schemas import ApprovalKind


@dataclass(frozen=True)
class Node:
    name: str
    stage: str
    permission: str = "run.start"
    risk: str = "low"
    approval: ApprovalKind | None = None


DEFINITION = (
    Node("create_project", "discovery"), Node("collect_requirements", "discovery"),
    Node("retrieve_product_knowledge", "discovery"), Node("gap_analysis", "discovery"),
    Node("generate_implementation_plan", "discovery"),
    Node("plan_approval", "discovery", risk="high", approval=ApprovalKind.PLAN),
    Node("inspect_tenant_configuration", "configuration"), Node("generate_configuration_changes", "configuration"),
    Node("configuration_approval", "configuration", risk="high", approval=ApprovalKind.CONFIGURATION),
    Node("apply_configuration", "migration", risk="high"), Node("validate_import_files", "migration"),
    Node("import_approval", "migration", risk="high", approval=ApprovalKind.IMPORT),
    Node("execute_import", "acceptance", permission="import.execute", risk="high"),
    Node("generate_training_materials", "acceptance"), Node("run_go_live_checks", "acceptance"),
    Node("acceptance_approval", "acceptance", risk="high", approval=ApprovalKind.ACCEPTANCE),
    Node("close_project", "closure", risk="high"),
)
NODES = [node.name for node in DEFINITION]
APPROVAL_NODES = {node.name: node.approval for node in DEFINITION if node.approval}
STAGES = {}
for node in DEFINITION:
    if not node.approval:
        STAGES.setdefault(node.stage, []).append(node.name)
