"""回测评分多实现：与 ``RecapStrategy`` 解耦，便于迭代与单测。"""
from __future__ import annotations

import re
import unicodedata
from typing import List

from stock_recap.domain.models import BacktestResult, MarketSnapshot, RecapStrategy


def _top_sector_names(actual_snapshot: MarketSnapshot) -> List[str]:
    sp = actual_snapshot.sector_performance or {}
    top_list: List[str] = []
    for item in sp.get("涨幅前10", []):
        name = item.get("板块名称") or item.get("name") or ""
        if name:
            top_list.append(name)
    return top_list


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", (s or "").strip()).lower()


def _strip_sector_noise(s: str) -> str:
    t = _norm(s)
    for suf in ("板块", "行业", "概念"):
        t = t.replace(suf, "")
    return t.strip()


def _predicted_cores(pred: str) -> List[str]:
    """从一条主线描述中拆出若干可匹配片段。"""
    raw = pred.replace("｜", "|")
    parts = re.split(r"[|，,、；;]+", raw)
    cores: List[str] = []
    for p in parts:
        c = _strip_sector_noise(p)
        if len(c) >= 2:
            cores.append(c)
    if not cores and pred.strip():
        cores.append(_strip_sector_noise(pred))
    return cores


class KeywordSubstringBacktestStrategy:
    """与历史 ``compute_backtest`` 一致的子串命中。"""

    @property
    def name(self) -> str:
        return "keyword_substring"

    def evaluate(
        self,
        *,
        strategy_date: str,
        strategy_recap: RecapStrategy,
        actual_date: str,
        actual_snapshot: MarketSnapshot,
    ) -> BacktestResult:
        predicted = strategy_recap.mainline_focus
        top_list = _top_sector_names(actual_snapshot)
        if not top_list:
            return BacktestResult(
                strategy_date=strategy_date,
                actual_date=actual_date,
                predicted_sectors=predicted,
                actual_top_sectors=[],
                hit_count=0,
                hit_rate=0.0,
                detail="实际板块数据不足，无法回测",
                scoring_impl=self.name,
            )

        hit_count = 0
        hit_detail: List[str] = []
        for pred in predicted:
            core = pred.replace("板块", "").replace("行业", "").replace("概念", "").strip()
            matched = [act for act in top_list if core in act or act in core]
            if matched:
                hit_count += 1
                hit_detail.append(f"✓ {pred} → {matched[0]}")
            else:
                hit_detail.append(f"✗ {pred}")

        hit_rate = hit_count / len(predicted) if predicted else 0.0
        detail = (
            f"预测 {len(predicted)} 个方向，命中 {hit_count} 个（{hit_rate:.0%}）\n"
            + "\n".join(hit_detail)
        )
        return BacktestResult(
            strategy_date=strategy_date,
            actual_date=actual_date,
            predicted_sectors=predicted,
            actual_top_sectors=top_list,
            hit_count=hit_count,
            hit_rate=round(hit_rate, 3),
            detail=detail,
            scoring_impl=self.name,
        )


class NormalizedTokenOverlapBacktestStrategy:
    """归一化 + 片段重叠：对「板块名｜涨跌幅…」等复合主线更稳。"""

    @property
    def name(self) -> str:
        return "normalized_overlap"

    def _hit_one_pred(self, pred: str, top_list: List[str]) -> tuple[bool, str]:
        cores = _predicted_cores(pred)
        if not cores:
            return False, ""

        for act in top_list:
            an = _strip_sector_noise(act)
            if not an:
                continue
            for core in cores:
                if len(core) >= 2 and (core in an or an in core):
                    return True, act
                # 2～4 字中文 token 粗切：取 core 中连续片段
                if len(core) <= 6 and core in an:
                    return True, act
                toks = re.findall(r"[\u4e00-\u9fff]{2,6}", core)
                for t in toks:
                    if t in an:
                        return True, act
        return False, ""

    def evaluate(
        self,
        *,
        strategy_date: str,
        strategy_recap: RecapStrategy,
        actual_date: str,
        actual_snapshot: MarketSnapshot,
    ) -> BacktestResult:
        predicted = strategy_recap.mainline_focus
        top_list = _top_sector_names(actual_snapshot)
        if not top_list:
            return BacktestResult(
                strategy_date=strategy_date,
                actual_date=actual_date,
                predicted_sectors=predicted,
                actual_top_sectors=[],
                hit_count=0,
                hit_rate=0.0,
                detail="实际板块数据不足，无法回测",
                scoring_impl=self.name,
            )

        hit_count = 0
        hit_detail: List[str] = []
        for pred in predicted:
            ok, matched = self._hit_one_pred(pred, top_list)
            if ok:
                hit_count += 1
                hit_detail.append(f"✓ {pred} → {matched}")
            else:
                hit_detail.append(f"✗ {pred}")

        hit_rate = hit_count / len(predicted) if predicted else 0.0
        detail = (
            f"[{self.name}] 预测 {len(predicted)} 个方向，命中 {hit_count} 个（{hit_rate:.0%}）\n"
            + "\n".join(hit_detail)
        )
        return BacktestResult(
            strategy_date=strategy_date,
            actual_date=actual_date,
            predicted_sectors=predicted,
            actual_top_sectors=top_list,
            hit_count=hit_count,
            hit_rate=round(hit_rate, 3),
            detail=detail,
            scoring_impl=self.name,
        )
