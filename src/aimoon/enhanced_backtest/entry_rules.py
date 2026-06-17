"""Entry rule phases extracted from EnhancedBacktestEngine.

Phase 0: execute pending entry orders (T+1 open)
Phase 4: open new positions when slots are available
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from aimoon.enhanced_backtest.helpers import (
    RECENT_RET_THRESHOLD as _RECENT_RET_THRESHOLD,
)
from aimoon.enhanced_backtest.helpers import (
    ROC5_DROP_THRESHOLD as _ROC5_DROP_THRESHOLD,
)
from aimoon.enhanced_backtest.helpers import (
    ROC5_MODERATE_DROP as _ROC5_MODERATE_DROP,
)
from aimoon.enhanced_backtest.helpers import (
    ROC5_RISE_THRESHOLD as _ROC5_RISE_THRESHOLD,
)
from aimoon.enhanced_backtest.models import EnhancedPosition
from aimoon.enhanced_backtest.position import compute_position_weights

logger = logging.getLogger(__name__)


def phase4_open_replacements(
    bar_date: pd.Timestamp,
    prev_date: pd.Timestamp | None,
    positions: dict[str, EnhancedPosition],
    pending_entries: dict[str, dict],
    klines: dict[str, pd.DataFrame],
    trades: list,
    names: dict[str, str],
    sector_map: dict[str, str],
    engine: Any,
    alpha_signals: dict[str, Any] | None = None,
    sector_ctx: dict[str, Any] | None = None,
    recent_exits: dict[str, int] | None = None,
    stop_loss_count: dict[str, int] | None = None,
    effective_positions: int = 4,
    effective_threshold: int = 60,
    current_regime: str = "sideways",
    dd_scale: float = 1.0,
    bar_count: int = 0,
    rumi_signals: dict[str, Any] | None = None,
) -> None:
    """Open new positions to fill slots below max_positions."""
    recent_exits = recent_exits or {}
    stop_loss_count = stop_loss_count or {}

    # 当前 bar 的入场信号基于上一个交易日的评分（避免使用当日未完结数据）
    # 首 bar 时 prev_date 为 None，使用 bar_date 作为回退（评分窗口只看 [:idx+1]）
    alpha_query_date = prev_date if prev_date is not None else bar_date
    alpha_sigs = (
        engine._get_alpha_signals_for_date(alpha_signals, alpha_query_date)
        if alpha_signals
        else None
    )
    sector_exposure: dict[str, float] = {}
    for pos in positions.values():
        sec = pos.sector if pos.sector else ""
        if sec:
            sector_exposure[sec] = sector_exposure.get(sec, 0.0) + pos.weight

    scored_candidates: list[tuple[str, str, int]] = []
    ml_sigs = engine._get_ml_scores_for_date(alpha_query_date)

    candidate_codes: list[str] = []
    if ml_sigs:
        sorted_by_ml = sorted(ml_sigs.items(), key=lambda x: x[1], reverse=True)
        candidate_codes = [code for code, _ in sorted_by_ml[:30]]
    else:
        # 无 ML 预筛选时限制候选池，避免对全部股票评分（可数千只）
        candidate_codes = list(klines.keys())[:50]

    for code in candidate_codes:
        df = klines.get(code)
        if df is None or code == engine.benchmark_code or code in positions:
            continue
        if code in recent_exits and (bar_count - recent_exits[code]) < 5:
            continue
        sl_count = stop_loss_count.get(code, 0)
        if sl_count >= 2:
            continue
        elif sl_count > 0 and code in recent_exits:
            if (bar_count - recent_exits[code]) < engine.stop_loss_cooldown:
                continue
        if bar_date not in df.index:
            continue
        idx = df.index.get_loc(bar_date)
        if idx < 60:
            continue
        window = df.iloc[: idx + 1]
        if len(window) < 60:
            continue

        if ml_sigs and code in ml_sigs and ml_sigs[code] < 30:
            continue

        try:
            if len(window) >= 10:
                close_s = window["close"].dropna()
                if len(close_s) >= 10:
                    recent_ret = (close_s.iloc[-1] - close_s.iloc[-6]) / close_s.iloc[-6]
                    if recent_ret < _RECENT_RET_THRESHOLD:
                        continue
        except (IndexError, KeyError):
            pass

        # ── 核心趋势过滤器：强制均线多头排列方可开仓 ──
        try:
            if len(window) >= 60:
                close_s = window["close"].dropna()
                ma20 = close_s.rolling(20).mean()
                ma60 = close_s.rolling(60).mean()
                last_close = close_s.iloc[-1]
                last_ma20 = ma20.iloc[-1]
                last_ma60 = ma60.iloc[-1]
                if pd.isna(last_ma60) or pd.isna(last_ma20):
                    # 历史不足以计算MA60，放行
                    pass
                elif last_close < last_ma60 or last_ma20 < last_ma60:
                    # 价格 < MA60 或 MA20 < MA60 → 空头排列，禁止开仓
                    logger.debug(
                        "Trend filter blocked %s: close=%.2f<MA60=%.2f or MA20=%.2f<MA60=%.2f",
                        code,
                        last_close,
                        last_ma60,
                        last_ma20,
                        last_ma60,
                    )
                    continue
        except (IndexError, KeyError, ValueError):
            pass

        score = engine._score_stock(
            code,
            names.get(code, code),
            window,
            ctx=sector_ctx,
            alpha_signals=alpha_sigs,
            ml_scores=ml_sigs,
            regime=current_regime,
        )
        if score is not None and score >= effective_threshold:
            scored_candidates.append((code, names.get(code, code), score))

    rps_bonus: dict[str, float] = {}
    if len(scored_candidates) >= 5:
        roc_data: dict[str, dict[int, float]] = {}
        for code, _, _ in scored_candidates:
            if code not in klines or bar_date not in klines[code].index:
                continue
            df = klines[code]
            loc = df.index.get_loc(bar_date)
            if loc < 20:
                continue
            close = pd.to_numeric(df["close"].iloc[: loc + 1], errors="coerce")
            rocs: dict[int, float] = {}
            for period in [5, 10, 20]:
                if len(close) > period and close.iloc[-period - 1] > 0:
                    rocs[period] = float(
                        (close.iloc[-1] - close.iloc[-period - 1]) / close.iloc[-period - 1] * 100
                    )
            if rocs:
                roc_data[code] = rocs
        for period in [5, 10, 20]:
            pvals = {c: v[period] for c, v in roc_data.items() if period in v}
            if len(pvals) < 5:
                continue
            scodes = sorted(pvals, key=lambda c: pvals[c])
            total = len(scodes)
            for rank, code in enumerate(scodes):
                pct = (rank + 1) / total * 100
                if pct >= 90:
                    rps_bonus[code] = rps_bonus.get(code, 0) + 2
                elif pct >= 75:
                    rps_bonus[code] = rps_bonus.get(code, 0) + 1
                elif pct <= 10:
                    rps_bonus[code] = rps_bonus.get(code, 0) - 2
                elif pct <= 25:
                    rps_bonus[code] = rps_bonus.get(code, 0) - 1

    scores = {c: s + rps_bonus.get(c, 0) for c, _, s in scored_candidates}
    reversal_bonus: dict[str, float] = {}
    for code in scores:
        if code not in klines or bar_date not in klines[code].index:
            continue
        df = klines[code]
        loc = df.index.get_loc(bar_date)
        if loc < 5:
            continue
        close_5d = pd.to_numeric(df["close"].iloc[loc - 5 : loc + 1], errors="coerce")
        if len(close_5d) >= 2 and close_5d.iloc[0] > 0:
            roc5 = (close_5d.iloc[-1] - close_5d.iloc[0]) / close_5d.iloc[0]
            if roc5 < _ROC5_DROP_THRESHOLD:
                reversal_bonus[code] = 5
            elif roc5 < _ROC5_MODERATE_DROP:
                reversal_bonus[code] = 3
            elif roc5 > _ROC5_RISE_THRESHOLD:
                reversal_bonus[code] = -5

    for code, bonus in reversal_bonus.items():
        if code in scores:
            scores[code] += bonus

    rumi_bonus: dict[str, float] = {}
    if rumi_signals:
        for code in scores:
            if code in rumi_signals:
                rs = rumi_signals[code]
                rumi_score = getattr(rs, "rumi_score", 0)
                if rumi_score >= 80:
                    rumi_bonus[code] = 5
                elif rumi_score >= 70:
                    rumi_bonus[code] = 3
                elif rumi_score >= 60:
                    rumi_bonus[code] = 1
                elif rumi_score <= 20:
                    rumi_bonus[code] = -5

    for code, bonus in rumi_bonus.items():
        if code in scores:
            scores[code] += bonus

    if scores:
        historical_trades = [
            t for t in trades if pd.Timestamp(t.entry_date) < pd.Timestamp(bar_date)
        ]
        weights = compute_position_weights(
            trades=historical_trades,
            max_positions=effective_positions,
            klines=klines,
            scores=scores,
            use_kelly=engine.use_kelly,
            regime=current_regime,
        )
        # 加权仓位分配：按排名分配，第1名40%、第2名30%、第3-4名各15%
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        rank_weights = [0.40, 0.30, 0.15, 0.15]
        slots = int(effective_positions) - len(positions)
        for idx, (code, score) in enumerate(ranked):
            if slots <= 0:
                break
            sector = sector_map.get(code, "")
            weight = rank_weights[idx] if idx < 4 else weights.get(code, 1.0 / effective_positions)
            if sector:
                cur_sec = sector_exposure.get(sector, 0.0)
                if cur_sec + weight > engine.max_sector_pct:
                    continue
                sector_exposure[sector] = cur_sec + weight

            pending_entries[code] = {
                "name": names.get(code, code),
                "weight": weight,
                "sector": sector,
                "score": score,
                "signal_date": bar_date,
            }
            slots -= 1
