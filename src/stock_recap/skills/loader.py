"""Skills 注册表加载（agentskills 风格：manifest + SKILL.md）。

- ``manifest.json``：版本、mode → skill_id、技能列表。
- 每个技能目录下 ``SKILL.md``：可选 YAML frontmatter（``---`` 块）+ Markdown 正文。

扩展：新增目录与 ``SKILL.md``，在 manifest 的 ``skills`` 与 ``mode_to_skill_id`` 中登记即可。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any, Dict, List, Optional, Tuple

from stock_recap.domain.models import Mode

logger = logging.getLogger("stock_recap.skills.loader")

_PKG = "stock_recap.skills"


@dataclass(frozen=True)
class SkillDocument:
    """单条 skill 的解析结果。"""

    skill_id: str
    name: str
    description: str
    version: str
    body: str


@lru_cache(maxsize=1)
def _manifest() -> Dict[str, Any]:
    raw = files(_PKG).joinpath("manifest.json").read_text(encoding="utf-8")
    return json.loads(raw)


def skill_bundle_version() -> str:
    return str(_manifest().get("bundle_version", "0"))


def _parse_frontmatter(raw: str) -> Tuple[Dict[str, str], str]:
    """解析可选 YAML-like frontmatter（简单 ``key: value`` 行），无 PyYAML 依赖。"""
    text = raw.strip()
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    fm_block, body = parts[1], parts[2]
    meta: Dict[str, str] = {}
    for line in fm_block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*:\s*(.*)$", line)
        if m:
            meta[m.group(1).strip()] = m.group(2).strip().strip('"').strip("'")
    return meta, body.strip()


def _skill_path(skill_id: str) -> Optional[str]:
    for s in _manifest().get("skills", []):
        if s.get("id") == skill_id:
            return str(s.get("path", ""))
    return None


def load_skill_document(skill_id: str) -> Optional[SkillDocument]:
    """读取并解析单个 skill。"""
    rel = _skill_path(skill_id)
    if not rel:
        logger.warning("unknown skill id: %s", skill_id)
        return None
    try:
        raw = files(_PKG).joinpath(rel).read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("failed to read skill %s: %s", skill_id, e)
        return None
    meta, body = _parse_frontmatter(raw)
    return SkillDocument(
        skill_id=skill_id,
        name=meta.get("name", skill_id),
        description=meta.get("description", ""),
        version=meta.get("version", ""),
        body=body,
    )


def resolve_skill_id_for_mode(mode: Mode, override_skill_id: Optional[str] = None) -> Optional[str]:
    """由运行 mode（或覆盖 id）得到 manifest 中的 skill_id。"""
    if override_skill_id:
        return override_skill_id
    m = _manifest().get("mode_to_skill_id") or {}
    sid = m.get(mode)
    return str(sid) if sid else None


def load_skill_overlay_for_mode(
    mode: Mode,
    override_skill_id: Optional[str] = None,
) -> Optional[SkillDocument]:
    """返回当前 mode 应注入 system prompt 的 skill 文档（无则 None）。"""
    sid = resolve_skill_id_for_mode(mode, override_skill_id=override_skill_id)
    if not sid:
        return None
    doc = load_skill_document(sid)
    if doc is None or not doc.body.strip():
        return None
    return doc


def list_registered_skills() -> List[Dict[str, Any]]:
    """供运维/调试列出已登记技能。"""
    return list(_manifest().get("skills", []))
