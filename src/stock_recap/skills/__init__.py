"""
Agent Skills 包（业界常见「manifest + SKILL.md」形态）。

- 通用写作与数据约束：``stock_recap.resources.prompts``（偏「底座」）
- 任务级规程与扩展点：本包（偏「可插拔 skill」）

运行时由 ``infrastructure.llm.prompts`` 将 skill 正文叠加进 system prompt。
"""

from stock_recap.skills.loader import (
    SkillDocument,
    list_registered_skills,
    load_skill_document,
    load_skill_overlay_for_mode,
    resolve_skill_id_for_mode,
    skill_bundle_version,
)

__all__ = [
    "SkillDocument",
    "list_registered_skills",
    "load_skill_document",
    "load_skill_overlay_for_mode",
    "resolve_skill_id_for_mode",
    "skill_bundle_version",
]
