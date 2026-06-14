"""????????? ? ?????? + ?? Prometheus ???

????:
    1. ??????????? Python ????
    2. ?? prometheus_client ???pip install ??????
    3. ?????????????????
    4. ???? §7.3 ?????

????:
    from aimoon.metrics import get_metrics

    metrics = get_metrics()
    metrics.observe("backtest_duration", 12.5)
    metrics.increment("factor_computed")
    print(metrics.summary())
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server
    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False


@dataclass
class _MetricValue:
    """???????????"""
    values: list[float] = field(default_factory=list)
    max_window: int = 1000
    count: int = 0

    def observe(self, value: float) -> None:
        self.values.append(value)
        self.count += 1
        if len(self.values) > self.max_window:
            self.values = self.values[-self.max_window:]

    @property
    def last(self) -> float:
        return self.values[-1] if self.values else 0.0

    @property
    def mean(self) -> float:
        return statistics.mean(self.values) if self.values else 0.0

    @property
    def median(self) -> float:
        return statistics.median(self.values) if self.values else 0.0

    @property
    def stdev(self) -> float:
        return statistics.stdev(self.values) if len(self.values) >= 2 else 0.0

    @property
    def p95(self) -> float:
        if not self.values:
            return 0.0
        sorted_vals = sorted(self.values)
        idx = int(len(sorted_vals) * 0.95)
        return sorted_vals[min(idx, len(sorted_vals) - 1)]

    @property
    def p99(self) -> float:
        if not self.values:
            return 0.0
        sorted_vals = sorted(self.values)
        idx = int(len(sorted_vals) * 0.99)
        return sorted_vals[min(idx, len(sorted_vals) - 1)]

    def to_dict(self) -> dict[str, float]:
        return {
            "count": self.count,
            "last": self.last,
            "mean": self.mean,
            "median": self.median,
            "stdev": self.stdev,
            "p95": self.p95,
            "p99": self.p99,
        }


class MetricsCollector:
    """???????????"""

    def __init__(self) -> None:
        self._metrics: dict[str, _MetricValue] = defaultdict(_MetricValue)
        self._counters: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()
        self._start_time = time.time()

        # Prometheus ?????????
        self._prom_gauges: dict[str, Any] = {}
        self._prom_counters: dict[str, Any] = {}
        self._prom_histograms: dict[str, Any] = {}

    def observe(self, name: str, value: float) -> None:
        """????????"""
        with self._lock:
            self._metrics[name].observe(value)

        if _HAS_PROMETHEUS:
            self._observe_prometheus(name, value)

    def increment(self, name: str, value: int = 1) -> None:
        """??????"""
        with self._lock:
            self._counters[name] += value

    def gauge(self, name: str, value: float) -> None:
        """??????"""
        with self._lock:
            self._metrics[name].values = [value]
            self._metrics[name].count += 1

        if _HAS_PROMETHEUS:
            self._gauge_prometheus(name, value)

    def timer(self, name: str):
        """????????????"""

        class _TimerContext:
            def __init__(self, collector: Any, timer_name: str) -> None:
                self._collector = collector
                self._name = timer_name
                self._start = 0.0

            def __enter__(self) -> _TimerContext:
                self._start = time.time()
                return self

            def __exit__(self, *args: Any) -> None:
                elapsed = time.time() - self._start
                self._collector.observe(self._name, elapsed)

        return _TimerContext(self, name)

    def summary(self) -> dict[str, Any]:
        """???????"""
        with self._lock:
            result: dict[str, Any] = {
                "uptime_seconds": time.time() - self._start_time,
                "counters": dict(self._counters),
                "metrics": {},
            }
            for name, mv in self._metrics.items():
                result["metrics"][name] = mv.to_dict()
            return result

    def summary_text(self) -> str:
        """???????????"""
        s = self.summary()
        lines = ["=== Metrics Summary ===", f"Uptime: {s['uptime_seconds']:.0f}s"]

        if s["counters"]:
            lines.append("\n--- Counters ---")
            for k, v in sorted(s["counters"].items()):
                lines.append(f"  {k}: {v}")

        if s["metrics"]:
            lines.append("\n--- Timing ---")
            for k in sorted(s["metrics"].keys()):
                m = s["metrics"][k]
                lines.append(
                    f"  {k}: count={m['count']}, mean={m['mean']:.3f}s, "
                    f"p95={m['p95']:.3f}s, p99={m['p99']:.3f}s"
                )

        return "\n".join(lines)

    def observe_dict(self, prefix: str, data: dict[str, float]) -> None:
        """?????????"""
        for key, value in data.items():
            self.observe(f"{prefix}_{key}", value)

    def _observe_prometheus(self, name: str, value: float) -> None:
        """??? Prometheus???????"""
        try:
            safe_name = name.replace(".", "_").replace("-", "_")
            if safe_name not in self._prom_histograms:
                self._prom_histograms[safe_name] = Histogram(
                    f"aimoon_{safe_name}",
                    f"Metric: {name}",
                    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0),
                )
            self._prom_histograms[safe_name].observe(value)
        except Exception:
            pass

    def _gauge_prometheus(self, name: str, value: float) -> None:
        """?? Prometheus Gauge?"""
        try:
            safe_name = name.replace(".", "_").replace("-", "_")
            if safe_name not in self._prom_gauges:
                self._prom_gauges[safe_name] = Gauge(
                    f"aimoon_{safe_name}",
                    f"Gauge: {name}",
                )
            self._prom_gauges[safe_name].set(value)
        except Exception:
            pass

    def start_prometheus_server(self, port: int = 9090) -> None:
        """?? Prometheus HTTP ???"""
        if not _HAS_PROMETHEUS:
            logger.warning("prometheus_client not installed, cannot start server")
            return
        try:
            start_http_server(port)
            logger.info("Prometheus metrics server started on port %d", port)
        except Exception as e:
            logger.error("Failed to start Prometheus server: %s", e)


# ????
_metrics_collector: MetricsCollector | None = None
_metrics_lock = threading.Lock()


def get_metrics() -> MetricsCollector:
    """???? MetricsCollector ???"""
    global _metrics_collector
    if _metrics_collector is None:
        with _metrics_lock:
            if _metrics_collector is None:
                _metrics_collector = MetricsCollector()
    return _metrics_collector


def record_backtest_metrics(
    duration_seconds: float,
    n_stocks: int,
    n_days: int,
    total_return: float,
    sharpe: float,
    max_drawdown: float,
    win_rate: float,
    n_trades: int,
) -> None:
    """?????????"""
    m = get_metrics()
    m.observe("backtest_duration_seconds", duration_seconds)
    m.observe("backtest_stocks", float(n_stocks))
    m.observe("backtest_days", float(n_days))
    m.observe("backtest_total_return_pct", total_return)
    m.observe("backtest_sharpe", sharpe)
    m.observe("backtest_max_drawdown_pct", max_drawdown)
    m.observe("backtest_win_rate", win_rate)
    m.observe("backtest_trade_count", float(n_trades))
    m.increment("backtest_runs")
