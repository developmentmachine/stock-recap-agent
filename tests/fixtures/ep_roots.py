"""供 entry point 测试：返回 skill bundle 根目录。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent / "ep_skill_bundle"


def bundle_root() -> Path:
    return ROOT
