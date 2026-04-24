"""Golden snapshot 比对工具：``assert_matches_golden(name, actual)``。

约定：
- 所有 golden 文件存于 ``tests/golden/data/``；
- 文本文件按 utf-8 / LF 行尾对比；JSON 用 ``sort_keys+ensure_ascii=False`` 标准化；
- 设置环境变量 ``RECAP_GOLDEN_UPDATE=1`` 时不做断言，而是把 actual 写回 golden 文件，
  便于一次性升级（升级前请 ``git diff`` 人工 review）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

GOLDEN_DIR = Path(__file__).resolve().parent / "data"


def _golden_path(name: str) -> Path:
    return GOLDEN_DIR / name


def _normalize_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def assert_matches_golden_text(name: str, actual: str) -> None:
    """文本 golden 比对（保留换行）。"""
    p = _golden_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    if os.environ.get("RECAP_GOLDEN_UPDATE") == "1":
        p.write_text(actual, encoding="utf-8")
        return
    assert p.exists(), (
        f"Golden 文件不存在：{p}。第一次运行时设置 RECAP_GOLDEN_UPDATE=1 生成。"
    )
    expected = p.read_text(encoding="utf-8")
    assert actual == expected, (
        f"Golden 不一致：{name}\n"
        f"--- expected ---\n{expected}\n--- actual ---\n{actual}\n"
        f"如确认改动符合预期，请运行 RECAP_GOLDEN_UPDATE=1 pytest 重写 {name}。"
    )


def assert_matches_golden_json(name: str, actual: Any) -> None:
    """JSON golden 比对：标准化后逐字符比，避免 dict 顺序 / 浮点表示差异。"""
    actual_str = _normalize_json(actual)
    p = _golden_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    if os.environ.get("RECAP_GOLDEN_UPDATE") == "1":
        p.write_text(actual_str, encoding="utf-8")
        return
    assert p.exists(), (
        f"Golden 文件不存在：{p}。第一次运行时设置 RECAP_GOLDEN_UPDATE=1 生成。"
    )
    expected_str = p.read_text(encoding="utf-8")
    if actual_str != expected_str:
        # 把语义比较也算一道防线：dict 顺序不影响判断
        try:
            expected_obj = json.loads(expected_str)
            if expected_obj == json.loads(actual_str):
                return
        except json.JSONDecodeError:
            pass
        raise AssertionError(
            f"Golden JSON 不一致：{name}\n"
            f"--- expected ---\n{expected_str}\n--- actual ---\n{actual_str}\n"
            f"如确认改动符合预期，请运行 RECAP_GOLDEN_UPDATE=1 pytest 重写 {name}。"
        )


__all__ = [
    "GOLDEN_DIR",
    "assert_matches_golden_json",
    "assert_matches_golden_text",
]
