"""进程内 Prometheus 风格指标注册与暴露。

为什么不直接接 ``opentelemetry.metrics`` / ``prometheus_client``：
- 我们目前是单进程 / 单容器部署，业务侧需要的就是 counter + histogram + 标签；
- 引入 ``prometheus_client`` 会带来 multiprocess gunicorn 的复杂度，且收益有限；
- 引入 OTel metrics 需要再装 ``opentelemetry-exporter-prometheus``，与现有 OTel
  trace exporter 分离维护。

本模块以最小代价提供：
1. ``Counter`` / ``Histogram``（固定桶）、按 ``frozenset(label_pairs)`` 维度聚合；
2. 线程安全（``RLock`` 全局保护，业务调用频率不高）；
3. ``render_prometheus`` 直出 prometheus 0.0.4 文本格式，可被 Prometheus / VictoriaMetrics 抓取；
4. 便捷 ``record_*`` 包装函数 —— 调用方不感知 registry，只暴露稳定语义。

未来需要切到完整 OTel metrics 时，只需替换 ``record_*`` 内部实现，调用点不动。
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple


# 标签键-值二元组的稳定序集合，作为聚合的子维度 key。
LabelTuple = Tuple[Tuple[str, str], ...]


def _normalize_labels(labels: Optional[Dict[str, str]]) -> LabelTuple:
    if not labels:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


@dataclass
class _CounterSeries:
    value: float = 0.0


@dataclass
class _HistogramSeries:
    """固定桶 histogram；``buckets`` 为 ``le`` 上界（不含 ``+Inf``）。"""

    buckets: Tuple[float, ...]
    bucket_counts: List[int] = field(default_factory=list)
    count: int = 0
    sum: float = 0.0

    def __post_init__(self) -> None:
        if not self.bucket_counts:
            self.bucket_counts = [0] * len(self.buckets)

    def observe(self, value: float) -> None:
        self.count += 1
        self.sum += float(value)
        for i, ub in enumerate(self.buckets):
            if value <= ub:
                self.bucket_counts[i] += 1


# 默认 histogram 桶（毫秒级别延迟，覆盖到 60s）。
_DEFAULT_LATENCY_BUCKETS_MS: Tuple[float, ...] = (
    5, 10, 25, 50, 100, 250, 500,
    1_000, 2_500, 5_000, 10_000, 30_000, 60_000,
)


@dataclass
class _MetricSpec:
    name: str
    help: str
    type: str  # "counter" | "histogram"
    buckets: Tuple[float, ...] = ()


class MetricsRegistry:
    """进程内单例式注册表；测试可以构造独立实例传入 ``record_*`` 注入。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._specs: Dict[str, _MetricSpec] = {}
        self._counters: Dict[str, Dict[LabelTuple, _CounterSeries]] = {}
        self._histograms: Dict[str, Dict[LabelTuple, _HistogramSeries]] = {}

    # ─── 注册 ─────────────────────────────────────────────────────────────

    def register_counter(self, name: str, help_text: str) -> None:
        with self._lock:
            self._specs.setdefault(name, _MetricSpec(name=name, help=help_text, type="counter"))
            self._counters.setdefault(name, {})

    def register_histogram(
        self,
        name: str,
        help_text: str,
        buckets: Iterable[float] = _DEFAULT_LATENCY_BUCKETS_MS,
    ) -> None:
        with self._lock:
            self._specs.setdefault(
                name,
                _MetricSpec(
                    name=name, help=help_text, type="histogram",
                    buckets=tuple(sorted(buckets)),
                ),
            )
            self._histograms.setdefault(name, {})

    # ─── 写入 ─────────────────────────────────────────────────────────────

    def inc_counter(
        self,
        name: str,
        value: float = 1.0,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        with self._lock:
            if name not in self._specs:
                # 未注册的 counter：自动注册（稳态会被显式注册覆盖 help 文案）。
                self.register_counter(name, help_text="")
            series = self._counters[name].setdefault(_normalize_labels(labels), _CounterSeries())
            series.value += float(value)

    def observe_histogram(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        with self._lock:
            if name not in self._specs:
                self.register_histogram(name, help_text="")
            spec = self._specs[name]
            series = self._histograms[name].setdefault(
                _normalize_labels(labels),
                _HistogramSeries(buckets=spec.buckets),
            )
            series.observe(float(value))

    # ─── 输出 ─────────────────────────────────────────────────────────────

    def render_prometheus(self) -> str:
        """Prometheus 0.0.4 exposition format。"""
        lines: List[str] = []
        with self._lock:
            for name in sorted(self._specs.keys()):
                spec = self._specs[name]
                if spec.type == "counter":
                    series_map = self._counters.get(name, {})
                    if spec.help:
                        lines.append(f"# HELP {name} {spec.help}")
                    lines.append(f"# TYPE {name} counter")
                    if not series_map:
                        lines.append(f"{name} 0")
                        continue
                    for labels, series in sorted(series_map.items()):
                        lines.append(f"{name}{_render_labels(labels)} {_fmt(series.value)}")
                elif spec.type == "histogram":
                    series_map = self._histograms.get(name, {})
                    if spec.help:
                        lines.append(f"# HELP {name} {spec.help}")
                    lines.append(f"# TYPE {name} histogram")
                    if not series_map:
                        lines.append(f"{name}_count 0")
                        lines.append(f"{name}_sum 0")
                        continue
                    for labels, series in sorted(series_map.items()):
                        for ub, c in zip(spec.buckets, series.bucket_counts):
                            le_labels = labels + (("le", _fmt(ub)),)
                            lines.append(
                                f"{name}_bucket{_render_labels(le_labels)} {c}"
                            )
                        # +Inf 桶
                        inf_labels = labels + (("le", "+Inf"),)
                        lines.append(
                            f"{name}_bucket{_render_labels(inf_labels)} {series.count}"
                        )
                        lines.append(f"{name}_sum{_render_labels(labels)} {_fmt(series.sum)}")
                        lines.append(f"{name}_count{_render_labels(labels)} {series.count}")
        return "\n".join(lines) + "\n"

    # ─── 内省（测试用） ───────────────────────────────────────────────────

    def counter_value(
        self, name: str, labels: Optional[Dict[str, str]] = None
    ) -> float:
        with self._lock:
            series = self._counters.get(name, {}).get(_normalize_labels(labels))
            return series.value if series else 0.0

    def histogram_count(
        self, name: str, labels: Optional[Dict[str, str]] = None
    ) -> int:
        with self._lock:
            series = self._histograms.get(name, {}).get(_normalize_labels(labels))
            return series.count if series else 0

    def histogram_sum(
        self, name: str, labels: Optional[Dict[str, str]] = None
    ) -> float:
        with self._lock:
            series = self._histograms.get(name, {}).get(_normalize_labels(labels))
            return series.sum if series else 0.0

    def reset(self) -> None:
        """仅供测试 / 热更新；生产请勿调用，会清掉抓取间累计值。"""
        with self._lock:
            self._counters = {n: {} for n in self._counters}
            self._histograms = {n: {} for n in self._histograms}


def _render_labels(labels: LabelTuple) -> str:
    if not labels:
        return ""
    parts = [f'{k}="{_escape_label(v)}"' for k, v in labels]
    return "{" + ",".join(parts) + "}"


def _escape_label(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt(x: float) -> str:
    if math.isinf(x):
        return "+Inf"
    if x == int(x):
        return str(int(x))
    return repr(x)


# ─── 全局单例 + 默认 spec 注册 ─────────────────────────────────────────────

_DEFAULT_REGISTRY: Optional[MetricsRegistry] = None
_DEFAULT_LOCK = threading.Lock()


def _build_default_registry() -> MetricsRegistry:
    reg = MetricsRegistry()
    # 业务级 metrics（含语义）
    reg.register_counter(
        "recap_runs_total",
        "Recap generate 调用总数（按 mode/provider/status 维度）。status: ok|failed|empty.",
    )
    reg.register_histogram(
        "recap_phase_duration_ms",
        "Agent 单个阶段执行时长（毫秒）。",
    )
    reg.register_counter(
        "llm_calls_total",
        "LLM call 次数。status: ok|transport_error|business_error|budget_exceeded|other.",
    )
    reg.register_counter(
        "llm_tokens_total",
        "LLM 输入/输出 token 累计。kind: input|output.",
    )
    reg.register_counter(
        "tool_invocations_total",
        "工具调用次数。status: ok|failed|denied|timeout.",
    )
    reg.register_counter(
        "outbox_actions_total",
        "Outbox（pending_actions）处理次数。status: done|failed|retry.",
    )
    return reg


def get_metrics() -> MetricsRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        with _DEFAULT_LOCK:
            if _DEFAULT_REGISTRY is None:
                _DEFAULT_REGISTRY = _build_default_registry()
    return _DEFAULT_REGISTRY


def reset_default_metrics() -> None:
    """供测试使用：清空累计值（保留 spec）。生产代码请勿调用。"""
    if _DEFAULT_REGISTRY is not None:
        _DEFAULT_REGISTRY.reset()


# ─── 便捷 record_* API（业务调用点用这些，调用点不感知 Registry） ──────────


def record_recap_run(mode: str, provider: str, status: str) -> None:
    get_metrics().inc_counter(
        "recap_runs_total",
        labels={"mode": mode, "provider": provider, "status": status},
    )


def record_phase_duration(phase: str, duration_ms: float) -> None:
    get_metrics().observe_histogram(
        "recap_phase_duration_ms",
        value=duration_ms,
        labels={"phase": phase},
    )


def record_llm_call(backend: str, status: str) -> None:
    get_metrics().inc_counter(
        "llm_calls_total",
        labels={"backend": backend, "status": status},
    )


def record_llm_tokens(backend: str, kind: str, n: int) -> None:
    if n <= 0:
        return
    get_metrics().inc_counter(
        "llm_tokens_total",
        value=float(n),
        labels={"backend": backend, "kind": kind},
    )


def record_tool_invocation(tool: str, status: str) -> None:
    get_metrics().inc_counter(
        "tool_invocations_total",
        labels={"tool": tool, "status": status},
    )


def record_outbox_action(action_type: str, status: str) -> None:
    get_metrics().inc_counter(
        "outbox_actions_total",
        labels={"action_type": action_type, "status": status},
    )


__all__ = [
    "MetricsRegistry",
    "get_metrics",
    "record_llm_call",
    "record_llm_tokens",
    "record_outbox_action",
    "record_phase_duration",
    "record_recap_run",
    "record_tool_invocation",
    "reset_default_metrics",
]
