"""Alpha Zoo 因子 → aimoon 信号转换器。

将截面因子值（宽表最后一行）转换为每只股票的 Signal 对象。
转换逻辑：提取最后一行 → 截面排名 → 百分位 → 分数。
"""
from __future__ import annotations

import logging

import pandas as pd

from aimoon.factors.registry import Registry, SkipAlpha, RegistryError
from aimoon.models import Signal

logger = logging.getLogger(__name__)


def compute_alpha_signals(
    registry: Registry,
    panel: dict[str, pd.DataFrame],
    target_date: pd.Timestamp | None = None,
) -> dict[str, list[Signal]]:
    """运行所有注册的 alpha 因子，转换为每只股票的 Signal 列表。

    Parameters
    ----------
    registry : Registry
        因子注册表。
    panel : dict[str, pd.DataFrame]
        Alpha Zoo 宽表格式的面板数据。
    target_date : pd.Timestamp | None
        指定日期的截面值。None 则取最后一行。

    Returns
    -------
    dict[str, list[Signal]]
        股票代码 -> 来自 alpha 因子的 Signal 列表。
    """
    if not panel or "close" not in panel:
        return {}

    codes = list(panel["close"].columns)
    if not codes:
        return {}

    factor_snapshots: dict[str, pd.Series] = {}

    for alpha_id in registry.list():
        try:
            factor_df = registry.compute(alpha_id, panel)
        except SkipAlpha:
            continue
        except RegistryError as exc:
            logger.debug("Alpha %s 计算失败: %s", alpha_id, exc)
            continue
        except Exception as exc:
            logger.debug("Alpha %s 异常: %s", alpha_id, exc)
            continue

        # 取指定日期或最后一行的截面值
        if target_date is not None and target_date in factor_df.index:
            row = factor_df.loc[target_date]
        else:
            row = factor_df.iloc[-1]
        if row.isna().all():
            continue
        factor_snapshots[alpha_id] = row

    if not factor_snapshots:
        return {}

    # 将每个因子的截面值转换为每只股票的 Signal
    signals_by_code: dict[str, list[Signal]] = {code: [] for code in codes}

    for alpha_id, snapshot in factor_snapshots.items():
        meta = registry.get(alpha_id).meta
        nickname = meta.get("nickname") or alpha_id
        themes = meta.get("theme", [])

        # 截面排名：将因子值转换为百分位
        ranked = snapshot.rank(pct=True, na_option="keep")

        for code in codes:
            if code not in ranked.index:
                continue
            pct = ranked[code]
            if pd.isna(pct):
                continue

            score = _pct_to_score(pct, themes)
            if score == 0:
                continue

            signal = Signal(
                name=f"alpha_{alpha_id}",
                label=f"α:{nickname}({pct:.0%})",
                score=score,
            )
            signals_by_code[code].append(signal)

    # 过滤空列表
    return {code: sigs for code, sigs in signals_by_code.items() if sigs}


def _pct_to_score(pct: float, themes: list[str]) -> int:
    """将百分位排名转换为 aimoon 分数（提升权重）。

    标准规则（因子值越高预测收益越高）：
    - ≥0.80: +3 (强看多)
    - ≥0.65: +2 (温和看多)
    - ≤0.20: -3 (强看空)
    - ≤0.35: -2 (温和看空)
    - 其他: 0 (不发出信号)

    对于反转类因子（theme 含 "reversal"），信号取反。
    """
    is_reversal = "reversal" in themes

    if pct >= 0.80:
        score = +3
    elif pct >= 0.65:
        score = +2
    elif pct <= 0.20:
        score = -3
    elif pct <= 0.35:
        score = -2
    else:
        return 0

    return -score if is_reversal else score
