"""Factor evaluation -- IC/ICIR analysis.

WARNING: This module uses FORWARD returns (future data) for evaluation.
It is intended for OFFLINE factor research and model diagnostics ONLY.
Do NOT use evaluate_all_scorers or evaluate_factor in live trading or
signal generation -- doing so would introduce look-ahead bias.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from aimoon.indicators.technical import TechInd
from aimoon.scoring import SCORERS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FactorEval:
    """单个因子的评估结果。"""

    name: str
    mean_ic: float  # 平均 IC（Spearman 秩相关系数）
    ic_std: float  # IC 标准差
    icir: float  # IC / std(IC)，衡量稳定性
    ic_positive_ratio: float  # IC > 0 的比例
    tier_returns: tuple[float, ...]  # 五分位收益（1-5 组）
    long_short: float  # top 组 - bottom 组 收益


def evaluate_factor(
    factor_values: dict[str, float],
    forward_returns: dict[str, float],
) -> FactorEval | None:
    """评估单个因子的 IC。返回 None 如果样本不足。"""
    common_codes = sorted(set(factor_values) & set(forward_returns))
    if len(common_codes) < 20:
        return None

    fv = np.array([factor_values[c] for c in common_codes])
    fr = np.array([forward_returns[c] for c in common_codes])

    # 去掉 NaN
    mask = ~(np.isnan(fv) | np.isnan(fr))
    fv, fr = fv[mask], fr[mask]
    if len(fv) < 20:
        return None

    ic, _ = stats.spearmanr(fv, fr)

    # 五分位收益
    n = len(fv)
    order = np.argsort(fv)
    quintile = np.zeros(n, dtype=int)
    for i in range(5):
        start = int(i * n / 5)
        end = int((i + 1) * n / 5)
        quintile[order[start:end]] = i
    tier_returns = tuple(
        float(fr[quintile == i].mean()) if (quintile == i).any() else 0.0 for i in range(5)
    )

    return FactorEval(
        name="",
        mean_ic=float(ic),
        ic_std=0.0,
        icir=0.0,
        ic_positive_ratio=0.5,
        tier_returns=tier_returns,
        long_short=tier_returns[4] - tier_returns[0] if len(tier_returns) == 5 else 0.0,
    )


def evaluate_all_scorers(
    universe_klines: dict[str, pd.DataFrame],
    forward_days: int = 22,
    eval_days: int = 60,
) -> list[FactorEval]:
    """对所有 scorer 做逐日 IC 分析，返回每个因子的汇总评估。

    Parameters
    ----------
    universe_klines : dict[str, pd.DataFrame]
        code -> 完整 K 线数据
    forward_days : int
        远期收益天数
    eval_days : int
        评估窗口（最近 N 天）
    """
    # 收集所有 scorer 的名称
    scorer_names: list[str] = []
    for scorer in SCORERS:
        name = getattr(scorer, "__name__", scorer.__class__.__name__)
        scorer_names.append(name)

    # 收集每个 scorer 在每个日期的 IC
    ic_series: dict[str, list[float]] = {name: [] for name in scorer_names}
    # 最终评估用的五分位收益
    final_fv: dict[str, dict[str, float]] = {name: {} for name in scorer_names}
    final_fr: dict[str, float] = {}

    # 取所有股票共同的日期范围
    all_dates = set()
    for kline in universe_klines.values():
        if len(kline) > 0:
            all_dates.update(kline.index[-eval_days:])
    sorted_dates = sorted(all_dates)
    if len(sorted_dates) < forward_days + 1:
        return []

    # 逐日评估
    for date_idx in range(len(sorted_dates) - forward_days):
        target_date = sorted_dates[date_idx]
        future_date = sorted_dates[date_idx + forward_days]

        factor_values_day: dict[str, dict[str, float]] = {name: {} for name in scorer_names}
        returns_day: dict[str, float] = {}

        for code, kline in universe_klines.items():
            if target_date not in kline.index or future_date not in kline.index:
                continue
            loc = kline.index.get_loc(target_date)
            if loc < 60:
                continue

            window = kline.iloc[: loc + 1]
            try:
                ti = TechInd(window)
            except Exception:
                continue

            # 远期收益
            close_now = float(kline.loc[target_date, "close"])
            close_future = float(kline.loc[future_date, "close"])
            if close_now > 0:
                returns_day[code] = (close_future - close_now) / close_now * 100

            # 每个 scorer 的因子值
            for scorer, name in zip(SCORERS, scorer_names):
                try:
                    result = scorer(ti, code=code, ctx=None)
                    if result is None:
                        continue
                    signals = result if isinstance(result, list) else [result]
                    factor_values_day[name][code] = sum(s.score for s in signals)
                except Exception:
                    continue

        # 计算当日 IC
        for name in scorer_names:
            common = sorted(set(factor_values_day[name]) & set(returns_day))
            if len(common) < 20:
                continue
            fv = np.array([factor_values_day[name][c] for c in common])
            fr = np.array([returns_day[c] for c in common])
            if np.std(fv) == 0 or np.std(fr) == 0:
                continue
            ic, _ = stats.spearmanr(fv, fr)
            if not np.isnan(ic):
                ic_series[name].append(ic)

        # 最后一天的因子值和远期收益用于五分位
        if date_idx == len(sorted_dates) - forward_days - 1:
            for name in scorer_names:
                final_fv[name] = factor_values_day[name].copy()
            final_fr = returns_day.copy()

    # 汇总
    results: list[FactorEval] = []
    for name in scorer_names:
        ics = ic_series[name]
        if len(ics) < 5:
            continue
        mean_ic = float(np.mean(ics))
        ic_std = float(np.std(ics))
        icir = mean_ic / ic_std if ic_std > 0 else 0.0
        pos_ratio = sum(1 for x in ics if x > 0) / len(ics)

        # 五分位收益（用最后一天数据）
        fv_final = final_fv[name]
        fr_final = final_fr
        eval_result = evaluate_factor(fv_final, fr_final)

        results.append(
            FactorEval(
                name=name,
                mean_ic=mean_ic,
                ic_std=ic_std,
                icir=icir,
                ic_positive_ratio=pos_ratio,
                tier_returns=eval_result.tier_returns if eval_result else (0.0,) * 5,
                long_short=eval_result.long_short if eval_result else 0.0,
            )
        )

    results.sort(key=lambda x: abs(x.mean_ic), reverse=True)
    return results


def compute_ic_weights(evals: list[FactorEval]) -> dict[str, float]:
    """从因子评估结果计算 IC 加权权重。"""
    if not evals:
        return {}
    # 只保留 mean_ic > 0.02 的因子
    valid = [e for e in evals if abs(e.mean_ic) > 0.02]
    if not valid:
        return {}
    # ICIR 加权
    total = sum(abs(e.icir) for e in valid)
    if total == 0:
        return {e.name: 1.0 / len(valid) for e in valid}
    return {e.name: abs(e.icir) / total for e in valid}
