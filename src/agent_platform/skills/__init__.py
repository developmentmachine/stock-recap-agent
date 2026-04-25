"""
Agent Skills 包（业界常见「manifest + SKILL.md」形态）。

- 通用写作与数据约束：``agent_platform.resources.prompts``（偏「底座」）
- 任务级规程与扩展点：本包（偏「可插拔 skill」）

运行时由 ``infrastructure.llm.prompts`` 将 skill 正文叠加进 system prompt。
"""

from agent_platform.skills.loader import (
    SkillDocument,
    clear_skill_manifest_cache,
    list_registered_skills,
    list_skill_bundle_roots_resolved,
    load_skill_document,
    load_skill_overlay_for_mode,
    resolve_skill_id_for_mode,
    skill_bundle_version,
)

__all__ = [
    "SkillDocument",
    "clear_skill_manifest_cache",
    "list_registered_skills",
    "list_skill_bundle_roots_resolved",
    "load_skill_document",
    "load_skill_overlay_for_mode",
    "resolve_skill_id_for_mode",
    "skill_bundle_version",
]
