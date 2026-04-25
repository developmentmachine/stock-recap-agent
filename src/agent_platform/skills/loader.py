"""Skills 注册表加载（agentskills 风格：manifest + SKILL.md）。

- **内置**：``agent_platform.skills.manifest.json`` + 包内相对 ``path``。
- **外部目录**：``RECAP_SKILL_EXTRA_DIRS`` 逗号分隔；每个根目录下须有自洽的
  ``manifest.json``（``skills`` / 可选 ``mode_to_skill_id``），路径与内置合并。
- **Entry points**：第三方包在 ``pyproject.toml`` 登记::

      [project.entry-points."agent_platform.skills"]
      my_bundle = "my_pkg.skills:bundle_root"

  ``bundle_root`` 可为 ``pathlib.Path`` / ``str``，或 **无参可调用** ``() -> Path | str``，
  指向含 ``manifest.json`` 的 bundle 根目录。

合并规则（W5-5）：
- ``skills``：按 ``id`` 后写覆盖先写；未出现过的 ``id`` 追加在列表末尾。
- ``mode_to_skill_id``：后写覆盖先写同一 ``mode``。
- ``bundle_version``：保留内置版本字符串（``skill_bundle_version()``），便于与
  prompt manifest 对齐；外部版本仅用于各自 manifest 审计。

缓存：进程内合并结果带指纹缓存；改环境变量或磁盘 manifest 后请调
``clear_skill_manifest_cache()``（测试 ``autouse`` fixture 已处理）。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import metadata
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from agent_platform.domain.models import Mode

logger = logging.getLogger("agent_platform.skills.loader")

_PKG = "agent_platform.skills"
_ENTRY_GROUP = "agent_platform.skills"

_MERGED_MANIFEST: Optional[Dict[str, Any]] = None
_MERGED_FINGERPRINT: Optional[Tuple[Any, ...]] = None


@dataclass(frozen=True)
class SkillDocument:
    """单条 skill 的解析结果。"""

    skill_id: str
    name: str
    description: str
    version: str
    body: str


def clear_skill_manifest_cache() -> None:
    """使合并 manifest 与包内缓存失效（测试 / 热加载后调用）。"""
    global _MERGED_MANIFEST, _MERGED_FINGERPRINT
    _MERGED_MANIFEST = None
    _MERGED_FINGERPRINT = None
    _base_manifest.cache_clear()


@lru_cache(maxsize=1)
def _base_manifest() -> Dict[str, Any]:
    raw = files(_PKG).joinpath("manifest.json").read_text(encoding="utf-8")
    return json.loads(raw)


def skill_bundle_version() -> str:
    """与内置 manifest 的 ``bundle_version`` 对齐（合并不改变该字符串）。"""
    return str(_base_manifest().get("bundle_version", "0"))


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


def _extra_dirs_from_settings() -> List[Path]:
    try:
        from agent_platform.config.settings import get_settings

        raw = (get_settings().skill_extra_dirs or "").strip()
    except Exception:
        raw = ""
    if not raw:
        return []
    return [Path(p.strip()).resolve() for p in raw.split(",") if p.strip()]


def _iter_entry_point_bundle_roots() -> List[Path]:
    roots: List[Path] = []
    try:
        eps = metadata.entry_points()
        selected = (
            eps.select(group=_ENTRY_GROUP)
            if hasattr(eps, "select")
            else eps.get(_ENTRY_GROUP, ())
        )
    except Exception as e:
        logger.debug("entry_points(%s) skipped: %s", _ENTRY_GROUP, e)
        return roots

    for ep in selected:
        try:
            obj = ep.load()
        except Exception as e:
            logger.warning(
                "failed to load agent_platform.skills entry point %s: %s", ep.name, e
            )
            continue
        try:
            if callable(obj):
                out = obj()
            else:
                out = obj
            root = Path(out).resolve()
        except Exception as e:
            logger.warning("bad skill bundle root from %s: %s", ep.name, e)
            continue
        if root.is_dir():
            roots.append(root)
        else:
            logger.warning("skill bundle root is not a directory: %s (%s)", root, ep.name)
    return roots


def _fingerprint() -> Tuple[Any, ...]:
    dirs = tuple(_extra_dirs_from_settings())
    try:
        eps = metadata.entry_points()
        selected = (
            eps.select(group=_ENTRY_GROUP)
            if hasattr(eps, "select")
            else eps.get(_ENTRY_GROUP, ())
        )
        ep_sig = tuple((ep.name, ep.value) for ep in sorted(selected, key=lambda x: x.name))
    except Exception:
        ep_sig = ()
    mtimes = []
    for d in dirs:
        mf = d / "manifest.json"
        try:
            mtimes.append((str(mf), mf.stat().st_mtime_ns if mf.is_file() else -1))
        except OSError:
            mtimes.append((str(mf), -2))
    for root in _iter_entry_point_bundle_roots():
        mf = root / "manifest.json"
        try:
            mtimes.append((str(mf), mf.stat().st_mtime_ns if mf.is_file() else -1))
        except OSError:
            mtimes.append((str(mf), -2))
    return (dirs, ep_sig, tuple(mtimes))


def _load_bundle_manifest(root: Path) -> Dict[str, Any]:
    mf = root / "manifest.json"
    if not mf.is_file():
        raise FileNotFoundError(f"missing manifest.json under {root}")
    return json.loads(mf.read_text(encoding="utf-8"))


def _merge_manifests(
    base: Dict[str, Any], overlays: List[Tuple[Dict[str, Any], Path]]
) -> Dict[str, Any]:
    out = json.loads(json.dumps(base))  # deep copy via JSON
    skills_out: List[Dict[str, Any]] = list(out.get("skills") or [])
    by_id: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for s in skills_out:
        sid = str(s.get("id", ""))
        if not sid:
            continue
        c = dict(s)
        c.pop("_bundle_root", None)
        by_id[sid] = c
        order.append(sid)

    modes: Dict[str, str] = dict(out.get("mode_to_skill_id") or {})

    for frag, root in overlays:
        for s in frag.get("skills") or []:
            sid = str(s.get("id", ""))
            if not sid:
                continue
            c = dict(s)
            c["_bundle_root"] = str(root)
            by_id[sid] = c
            if sid not in order:
                order.append(sid)
        for k, v in (frag.get("mode_to_skill_id") or {}).items():
            modes[str(k)] = str(v)

    out["skills"] = [by_id[i] for i in order if i in by_id]
    out["mode_to_skill_id"] = modes
    return out


def _merged_manifest() -> Dict[str, Any]:
    global _MERGED_MANIFEST, _MERGED_FINGERPRINT
    fp = _fingerprint()
    if _MERGED_MANIFEST is not None and fp == _MERGED_FINGERPRINT:
        return _MERGED_MANIFEST

    base = _base_manifest()
    overlays: List[Tuple[Dict[str, Any], Path]] = []
    for d in _extra_dirs_from_settings():
        try:
            overlays.append((_load_bundle_manifest(d), d))
        except Exception as e:
            logger.warning("skip invalid skill extra dir %s: %s", d, e)
    for root in _iter_entry_point_bundle_roots():
        try:
            overlays.append((_load_bundle_manifest(root), root))
        except Exception as e:
            logger.warning("skip invalid entry-point skill bundle %s: %s", root, e)

    merged = _merge_manifests(base, overlays) if overlays else json.loads(json.dumps(base))
    _MERGED_MANIFEST = merged
    _MERGED_FINGERPRINT = fp
    return merged


def _skill_record(skill_id: str) -> Optional[Dict[str, Any]]:
    for s in _merged_manifest().get("skills", []):
        if str(s.get("id")) == skill_id:
            return s
    return None


def load_skill_document(skill_id: str) -> Optional[SkillDocument]:
    """读取并解析单个 skill（包内或外部 bundle）。"""
    rec = _skill_record(skill_id)
    if not rec:
        logger.warning("unknown skill id: %s", skill_id)
        return None
    rel = str(rec.get("path", ""))
    if not rel:
        return None
    bundle_root = rec.get("_bundle_root")
    try:
        if bundle_root:
            raw = Path(str(bundle_root)).joinpath(rel).read_text(encoding="utf-8")
        else:
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
    m = _merged_manifest().get("mode_to_skill_id") or {}
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
    """供运维/调试列出已登记技能（不含内部 ``_bundle_root``）。"""
    out: List[Dict[str, Any]] = []
    for s in _merged_manifest().get("skills", []):
        out.append({k: v for k, v in s.items() if not str(k).startswith("_")})
    return out


def list_skill_bundle_roots_resolved() -> List[Union[str, Path]]:
    """调试：当前生效的外部 bundle 根（extra dirs + entry points，已 resolve）。"""
    roots: List[Union[str, Path]] = list(_extra_dirs_from_settings())
    roots.extend(_iter_entry_point_bundle_roots())
    return roots
