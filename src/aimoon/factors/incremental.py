"""增量因子计算引擎 — 新 K 线到达时仅更新受影响的因子。

核心策略:
1. 非截面因子 (无 rank/scale): 仅用该股票的历史数据，增量计算
2. 截面因子 (有 rank/scale): 需要所有股票同时刻值，批量更新最后一行
3. 按 max_window 分级: 短窗口因子立即更新，长窗口因子延迟

用法:
    engine = IncrementalFactorEngine(registry, panel)
    # 新 K 线到达
    updated = engine.on_new_bar(code, new_bar)
    # 批量截面更新
    cs_results = engine.batch_update_cross_sectional()
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from aimoon.factors.dag import build_factor_dag, get_affected_factors
from aimoon.factors.registry import Registry, RegistryError, SkipAlphaError

logger = logging.getLogger(__name__)


@dataclass
class FactorState:
    """单因子的增量计算状态。"""

    max_window: int
    is_cross_sectional: bool
    last_output: dict[str, float] = field(default_factory=dict)  # code -> value
    last_update_idx: int = -1  # 上次更新时的 panel 行号
    col_refs: tuple[str, ...] = ()


@dataclass
class IncrementalResult:
    """增量更新结果。"""

    updated_factors: int = 0
    skipped_factors: int = 0
    failed_factors: int = 0
    elapsed_ms: float = 0.0
    factor_values: dict[str, dict[str, float]] = field(default_factory=dict)


class IncrementalFactorEngine:
    """增量因子计算引擎。

    维护 panel 宽表和因子状态，新 K 线到达时仅重算受影响的因子。
    """

    def __init__(
        self,
        registry: Registry,
        panel: dict[str, pd.DataFrame],
    ):
        self.registry = registry
        self.panel = panel
        self.dag = build_factor_dag(registry)
        self._states: dict[str, FactorState] = {}
        self._lock = threading.Lock()

        self._init_states()

    def _init_states(self) -> None:
        """初始化所有因子的状态。"""
        for alpha_id in self.registry.list():
            node = self.dag.nodes.get(alpha_id)
            if node is None:
                continue
            self._states[alpha_id] = FactorState(
                max_window=node.max_window,
                is_cross_sectional=node.is_cross_sectional,
                col_refs=node.col_refs,
            )

    @property
    def panel_length(self) -> int:
        """当前 panel 的行数。"""
        close = self.panel.get("close")
        return len(close) if close is not None else 0

    def on_new_bar(
        self,
        code: str,
        new_bar: dict[str, float],
        bar_date: pd.Timestamp | None = None,
    ) -> IncrementalResult:
        """新 K 线到达，增量更新该股票的所有因子。

        Parameters
        ----------
        code : str
            股票代码。
        new_bar : dict
            新 K 线数据 {"open": x, "high": x, ...}。
        bar_date : pd.Timestamp, optional
            K 线日期，默认使用 new_bar 中的 "date" 或当前时间。

        Returns
        -------
        IncrementalResult
            更新结果统计。
        """
        t0 = time.monotonic()
        result = IncrementalResult()

        if bar_date is None:
            bar_date = new_bar.get("date", pd.Timestamp.now())
            if isinstance(bar_date, (int, float)):
                bar_date = pd.Timestamp.now()

        # 1. 追加到 panel 宽表
        with self._lock:
            self._append_to_panel(code, new_bar, bar_date)

        current_idx = self.panel_length - 1

        # 2. 确定变化的列
        changed_cols = {k for k in new_bar if k in self.panel and new_bar[k] is not None}

        # 3. 获取受影响的因子
        incremental_ids, cross_sectional_ids = get_affected_factors(self.dag, changed_cols)

        # 4. 增量更新非截面因子
        for alpha_id in incremental_ids:
            state = self._states.get(alpha_id)
            if state is None:
                result.skipped_factors += 1
                continue

            if not self._needs_recompute(alpha_id, code, current_idx):
                result.skipped_factors += 1
                continue

            try:
                value = self._compute_single_factor(alpha_id, code)
                if value is not None:
                    state.last_output[code] = value
                    state.last_update_idx = current_idx
                    result.updated_factors += 1
                    result.factor_values.setdefault(alpha_id, {})[code] = value
                else:
                    result.skipped_factors += 1
            except Exception as exc:
                logger.debug("Incremental compute failed for %s/%s: %s", alpha_id, code, exc)
                result.failed_factors += 1

        # 5. 截面因子标记为需要批量更新 (不在单股 on_new_bar 中处理)
        for alpha_id in cross_sectional_ids:
            state = self._states.get(alpha_id)
            if state is not None:
                state.last_update_idx = -1  # 标记需要重算

        result.elapsed_ms = (time.monotonic() - t0) * 1000
        return result

    def batch_update_cross_sectional(
        self,
        codes: list[str] | None = None,
    ) -> dict[str, pd.Series]:
        """批量更新所有截面因子的最后一行。

        截面因子 (rank, scale) 需要所有股票同时刻的值，
        不能单股增量，只能批量重算最后一行。

        Parameters
        ----------
        codes : list[str], optional
            要更新的股票代码列表，默认全部。

        Returns
        -------
        dict[str, pd.Series]
            {factor_id: Series[code -> value]} 最后一行的因子值。
        """
        results: dict[str, pd.Series] = {}
        current_idx = self.panel_length - 1

        for alpha_id, state in self._states.items():
            if not state.is_cross_sectional:
                continue

            # 检查是否需要更新
            if state.last_update_idx == current_idx:
                continue

            try:
                factor_df = self.registry.compute(alpha_id, self.panel)
                if factor_df is None or factor_df.empty:
                    continue

                last_row = factor_df.iloc[-1]
                results[alpha_id] = last_row

                # 更新状态
                state.last_output = last_row.dropna().to_dict()
                state.last_update_idx = current_idx

            except (SkipAlphaError, RegistryError) as exc:
                logger.debug("Cross-sectional compute failed for %s: %s", alpha_id, exc)
            except Exception as exc:
                logger.warning("Cross-sectional compute error for %s: %s", alpha_id, exc)

        return results

    def get_factor_value(self, alpha_id: str, code: str) -> float | None:
        """获取指定因子对指定股票的最新值。"""
        state = self._states.get(alpha_id)
        if state is None:
            return None
        return state.last_output.get(code)

    def get_all_factor_values(self, code: str) -> dict[str, float]:
        """获取指定股票的所有因子最新值。"""
        result: dict[str, float] = {}
        for alpha_id, state in self._states.items():
            val = state.last_output.get(code)
            if val is not None:
                result[alpha_id] = val
        return result

    def get_state_summary(self) -> dict[str, dict]:
        """获取所有因子状态摘要 (用于持久化)。"""
        summary: dict[str, dict] = {}
        for alpha_id, state in self._states.items():
            summary[alpha_id] = {
                "max_window": state.max_window,
                "is_cross_sectional": state.is_cross_sectional,
                "last_update_idx": state.last_update_idx,
                "n_stocks": len(state.last_output),
                "col_refs": list(state.col_refs),
            }
        return summary

    # ── 内部方法 ──

    def _append_to_panel(
        self,
        code: str,
        new_bar: dict[str, float],
        bar_date: pd.Timestamp,
    ) -> None:
        """追加新 K 线到 panel 宽表。"""
        for col_name, value in new_bar.items():
            if col_name == "date":
                continue
            wide = self.panel.get(col_name)
            if wide is None:
                continue
            if value is None or (isinstance(value, float) and np.isnan(value)):
                continue
            wide.loc[bar_date, code] = value

    def _needs_recompute(self, alpha_id: str, code: str, current_idx: int) -> bool:
        """判断因子是否需要重算。"""
        state = self._states.get(alpha_id)
        if state is None:
            return False

        node = self.dag.nodes.get(alpha_id)
        if node is None:
            return False

        # 首次计算
        if code not in state.last_output:
            return current_idx >= node.max_window

        # 时序因子: 每行都可能变化 → 必须重算
        if node.max_window > 0:
            return True

        return False

    def _compute_single_factor(
        self,
        alpha_id: str,
        code: str,
    ) -> float | None:
        """计算单个因子对单只股票的值。

        从宽表提取该股票的时序数据，构造窄表 panel，
        调用因子 compute() 并取最后一行的均值。
        """
        node = self.dag.nodes.get(alpha_id)
        if node is None:
            return None

        # 计算需要的历史长度
        lookback = max(node.max_window + 10, 30)

        # 提取单股窄表
        single_panel = self._extract_single_stock(code, lookback)
        if single_panel is None:
            return None

        # 惰性导入因子模块并计算
        try:
            alpha = self.registry.get(alpha_id)
            module = self.registry._load_module(alpha)
            compute_fn = getattr(module, "compute", None)
            if compute_fn is None:
                return None

            result = compute_fn(single_panel)
            if result is None or (isinstance(result, pd.DataFrame) and result.empty):
                return None

            # 取最后一行
            if isinstance(result, pd.DataFrame):
                last_row = result.iloc[-1]
                if last_row.isna().all():
                    return None
                return float(last_row.mean())
            elif isinstance(result, pd.Series):
                val = result.iloc[-1]
                return float(val) if pd.notna(val) else None
            else:
                return float(result)

        except (SkipAlphaError, RegistryError):
            return None
        except Exception as exc:
            logger.debug("Factor compute error %s/%s: %s", alpha_id, code, exc)
            return None

    def _extract_single_stock(
        self,
        code: str,
        lookback: int,
    ) -> dict[str, pd.DataFrame] | None:
        """从宽表提取单股窄表 panel。"""
        panel: dict[str, pd.DataFrame] = {}

        for col_name in ("open", "high", "low", "close", "volume", "amount"):
            wide = self.panel.get(col_name)
            if wide is None:
                if col_name in ("close", "volume"):
                    return None
                continue

            if code not in wide.columns:
                if col_name in ("close", "volume"):
                    return None
                continue

            series = wide[code].dropna()
            if len(series) < 5:
                return None

            series = series.tail(lookback)
            panel[col_name] = series.to_frame(name=code)

        return panel if panel else None

    def save(self, cache_dir: str | Path) -> None:
        """持久化因子状态到磁盘。"""
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)

        state_data = {}
        for alpha_id, state in self._states.items():
            state_data[alpha_id] = {
                "max_window": state.max_window,
                "is_cross_sectional": state.is_cross_sectional,
                "last_update_idx": state.last_update_idx,
                "col_refs": list(state.col_refs),
                "last_output": state.last_output,
            }

        path = cache_dir / "incremental_state.json"
        tmp_path = cache_dir / "incremental_state.json.tmp"
        try:
            tmp_path.write_text(json.dumps(state_data, indent=2), encoding="utf-8")
            tmp_path.replace(path)
        except OSError as exc:
            logger.warning("Failed to save incremental state: %s", exc)

    def load(self, cache_dir: str | Path) -> bool:
        """从磁盘恢复因子状态。"""
        path = Path(cache_dir) / "incremental_state.json"
        if not path.exists():
            return False

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for alpha_id, state_data in data.items():
                if alpha_id in self._states:
                    state = self._states[alpha_id]
                    state.last_update_idx = state_data.get("last_update_idx", -1)
                    state.last_output = state_data.get("last_output", {})
            return True
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Failed to load incremental state: %s", exc)
            return False


def create_incremental_engine(
    registry: Registry,
    klines: dict[str, pd.DataFrame],
    min_rows: int = 60,
) -> IncrementalFactorEngine | None:
    """创建增量引擎的便捷函数。

    从单股 K-line 数据构建 panel 并初始化引擎。

    Parameters
    ----------
    registry : Registry
        因子注册表。
    klines : dict[str, pd.DataFrame]
        {code: kline_df} 单股 K 线数据。
    min_rows : int
        最少行数阈值。

    Returns
    -------
    IncrementalFactorEngine | None
        初始化后的引擎，数据不足时返回 None。
    """
    from aimoon.factors.panel import build_panel

    panel = build_panel(klines, min_rows=min_rows)
    if panel is None:
        return None

    return IncrementalFactorEngine(registry, panel)
