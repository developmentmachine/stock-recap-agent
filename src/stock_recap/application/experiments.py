"""Prompt 实验：选 variant + 解析对应 prompt_version。

设计要点（W4-4 ）：
- DB 是事实源：``prompt_experiments`` + ``prompt_experiment_variants``；
- 分桶函数稳定可重放：同一 ``stickiness_key + experiment_id`` 永远落到同一 variant；
- 流量按 ``traffic_weight`` 加权（整数）；
- 当某个 mode 没有 active 实验时返回 None，调用方退化为 ``get_prompt_version`` 的全局活跃版本；
- 失败一律 fail-closed（None），不要影响主路径生成。

调用方：``application.orchestration.pipeline._phase_recall`` 在拿到 prompt_version 后
调一次 ``select_variant``，把结果写到 RecapAgentRunState；持久化层负责落库。
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Optional

from stock_recap.infrastructure.persistence.db import (
    load_active_experiment,
    load_experiment_variants,
)

logger = logging.getLogger("stock_recap.application.experiments")


@dataclass(frozen=True)
class VariantAssignment:
    experiment_id: str
    variant_id: str
    prompt_version: str  # 此 variant 绑定的 prompt 版本（覆盖全局活跃版本）


def _bucket_index(stickiness_key: str, experiment_id: str, total_weight: int) -> int:
    """按 ``sha1(experiment_id + ":" + stickiness_key)`` 取模分桶。

    用 sha1 而不是 hash() 是为了跨进程 / 重启稳定（CPython 默认 hash() 含 PYTHONHASHSEED 抖动）。
    """
    raw = f"{experiment_id}:{stickiness_key}".encode("utf-8")
    h = int(hashlib.sha1(raw).hexdigest()[:12], 16)
    return h % max(1, total_weight)


def select_variant(
    db_path: str,
    *,
    mode: str,
    stickiness_key: Optional[str],
) -> Optional[VariantAssignment]:
    """读 DB 选 variant；任何 DB / 配置异常返回 None（不打断生成）。"""
    if not stickiness_key:
        return None
    try:
        exp = load_active_experiment(db_path, mode=mode)
        if not exp:
            return None
        variants = load_experiment_variants(
            db_path, experiment_id=str(exp["experiment_id"])
        )
        if not variants:
            return None
        total = sum(int(v["traffic_weight"]) for v in variants)
        if total <= 0:
            return None
        idx = _bucket_index(stickiness_key, str(exp["experiment_id"]), total)
        cursor = 0
        for v in variants:
            cursor += int(v["traffic_weight"])
            if idx < cursor:
                return VariantAssignment(
                    experiment_id=str(exp["experiment_id"]),
                    variant_id=str(v["variant_id"]),
                    prompt_version=str(v["prompt_version"]),
                )
        # 兜底：理论不会走到（cursor 累加最终 = total）
        last = variants[-1]
        return VariantAssignment(
            experiment_id=str(exp["experiment_id"]),
            variant_id=str(last["variant_id"]),
            prompt_version=str(last["prompt_version"]),
        )
    except Exception as e:
        logger.warning(
            '{"event":"select_variant_failed","mode":"%s","error":%r}',
            mode,
            str(e),
        )
        return None


__all__ = ["VariantAssignment", "select_variant"]
