"""从包内资源加载 prompt 文本与 manifest（可版本化、可审计）。"""
from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files

_PKG = "agent_platform.resources.prompts"


@lru_cache(maxsize=1)
def _manifest() -> dict:
    raw = files(_PKG).joinpath("manifest.json").read_text(encoding="utf-8")
    return json.loads(raw)


def prompt_bundle_version() -> str:
    return str(_manifest()["bundle_version"])


@lru_cache(maxsize=8)
def load_prompt_artifact(name: str) -> str:
    """name 为 manifest.artifacts 的 key，如 system_recap。"""
    m = _manifest()
    rel = m.get("artifacts", {}).get(name)
    if not rel:
        raise KeyError(f"unknown prompt artifact: {name}")
    return files(_PKG).joinpath(rel).read_text(encoding="utf-8")


def system_recap_base() -> str:
    return load_prompt_artifact("system_recap").strip()


def pattern_extraction_system() -> str:
    return load_prompt_artifact("pattern_extraction_system").strip()


def json_output_instruction() -> str:
    return load_prompt_artifact("json_output_instruction").strip()


# 与历史代码/DB 中的 prompt_version 前缀对齐（包内 manifest）
PROMPT_BASE_VERSION: str = prompt_bundle_version()
