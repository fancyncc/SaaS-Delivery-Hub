"""Controlled, versioned implementation methods; never execute skill scripts."""
from __future__ import annotations

import hashlib
import re
from contextvars import ContextVar
from pathlib import Path

import yaml
from fastapi import HTTPException

from backend.config import get_settings
from backend.context_budget import count_tokens

ROOT = Path(__file__).resolve().parents[1] / "skills"
NODE_SKILLS = {
    **dict.fromkeys(("collect_requirements", "gap_analysis", "generate_implementation_plan", "check_project_brief"), "create-implementation-project-document"),
    **dict.fromkeys(("inspect_tenant_configuration", "generate_configuration_changes"), "review-configuration-proposal"),
    **dict.fromkeys(("validate_import_files", "preview_import"), "prepare-member-migration"),
    **dict.fromkeys(("generate_training_materials", "run_go_live_checks"), "prepare-training-and-acceptance"),
}
selected_skill: ContextVar[dict | None] = ContextVar("implementation_skill", default=None)


def catalog(root: Path | None = None) -> dict:
    root = (root or ROOT).resolve()
    result = {}
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.is_symlink() or not entry.resolve().is_relative_to(root):
            raise ValueError("Skill directory must remain inside the controlled root")
        source = entry / "SKILL.md"
        if not source.is_file():
            continue
        def read_resource(path, skill_directory=entry.resolve()):
            if (path.is_symlink() or not path.resolve().is_relative_to(skill_directory)
                    or not path.is_file() or path.stat().st_size > 64_000):
                raise ValueError("Invalid or oversized Skill resource")
            return path.read_text(encoding="utf-8")

        content = read_resource(source)
        match = re.match(r"\A---\s*\n(.*?)\n---\s*\n", content, re.S)
        if not match:
            raise ValueError("Invalid Skill frontmatter")
        try:
            metadata = yaml.safe_load(match[1])
        except yaml.YAMLError:
            raise ValueError("Invalid Skill metadata") from None
        if not isinstance(metadata, dict) or set(metadata) - {"name", "description", "license", "allowed-tools", "metadata"}:
            raise ValueError("Invalid Skill metadata fields")
        name = metadata.get("name", "")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64 or name != entry.name or name in result:
            raise ValueError("Invalid or duplicate Skill name")
        if not isinstance(metadata.get("description"), str) or not metadata["description"].strip() or len(metadata["description"]) > 1024:
            raise ValueError("Skill description is required")
        extra = metadata.get("metadata", {})
        if not isinstance(extra, dict) or not isinstance(extra.get("version", "1"), str):
            raise ValueError("Invalid Skill version metadata")
        resources = {}
        # Only referenced Markdown resources are instructions; scripts/assets are never run.
        refs = set(re.findall(r"(?:references|assets)/[A-Za-z0-9_./-]+\.md", content))
        for ref in sorted(refs):
            path = entry / ref
            if any(part.is_symlink() for part in [path, *path.parents] if part.is_relative_to(entry)):
                raise ValueError("Invalid Skill resource path")
            resources[ref] = read_resource(path)
        text = content + "\n" + "\n".join(resources.values())
        if len(text.encode()) > 64_000:
            raise ValueError("Skill resources exceed the size limit")
        result[name] = {"name": name, "description": metadata["description"],
                        "version": extra.get("version", "1"), "content": content,
                        "resources": resources, "hash": hashlib.sha256(text.encode()).hexdigest()}
    return result


def choose(items: dict, node: str) -> dict | None:
    value = items.get(NODE_SKILLS.get(node, ""))
    if value and count_tokens(value).tokens > get_settings().skill_max_tokens:
        raise HTTPException(422, "实施 Skill 超出上下文额度")
    return value


async def activate(session, run, node: str) -> dict | None:
    from backend.run_snapshots import inputs, record

    snapshot = await inputs(session, run)
    # Old runs never silently acquire new methods. Captured contents survive file changes.
    items = snapshot.payload.get("skills", {}) if snapshot else {}
    value = choose(items, node)
    selected_skill.set(value)
    if value:
        await record("skill", {"node": node, "name": value["name"], "version": value["version"], "hash": value["hash"]})
    return value


def instructions() -> str:
    value = selected_skill.get()
    if not value:
        return ""
    return ("\n使用以下受控实施方法编制材料；权限、审批和事实依据仍由业务代码决定。\n"
            + value["content"] + "\n" + "\n".join(value["resources"].values()))
