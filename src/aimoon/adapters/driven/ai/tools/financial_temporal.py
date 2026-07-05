"""财务时序分析工具（纯函数）。

输入近 N 年的历史 FinancialData,输出年度指标、3 年 CAGR、ROE 趋势、OCF 含金量与断点。
失败或数据不足时返回 ``{"__partial__": "<reason>"}``。
"""
from __future__ import annotations

import logging
import math

from aimoon.core.domain.entities.financial import FinancialData

logger = logging.getLogger(__name__)


def run(history: list[FinancialData] | None) -> dict[str, object]:
    try:
        if not history:
            return {"__partial__": "no_history", "n_years": 0, "years": []}

        years = [_serialize(f) for f in history]
        n_years = len(years)

        n_span = max(n_years - 1, 1)

        revenue_cagr = _cagr(*_endpoints(history, "revenue"), n=n_span)
        net_profit_cagr = _cagr(*_endpoints(history, "net_profit"), n=n_span)
        roe_trend = [_safe_div(f.net_profit, f.equity) for f in history]

        ocf_missing = any(f.operating_cf == 0.0 for f in history)
        ocf_profit_ratio: float
        if ocf_missing:
            ocf_profit_ratio = 0.0
        else:
            latest = history[0]
            ocf_profit_ratio = _safe_div(latest.operating_cf, latest.net_profit)

        return {
            "n_years": n_years,
            "years": years,
            "revenue_cagr": revenue_cagr,
            "net_profit_cagr": net_profit_cagr,
            "roe_trend": roe_trend,
            "ocf_profit_ratio": round(ocf_profit_ratio, 4),
            "ocf_partial": ocf_missing,
            "break_points": _detect_break_points(history),
        }
    except Exception as e:
        logger.debug("[financial_temporal] partial: %s: %s", type(e).__name__, e)
        return {"__partial__": "computation_error", "n_years": 0, "years": []}


def _serialize(f: FinancialData) -> dict[str, object]:
    return {
        "period": f.report_period,
        "revenue": f.revenue,
        "revenue_yoy": f.revenue_yoy,
        "net_profit": f.net_profit,
        "net_profit_yoy": f.net_profit_yoy,
        "roe": _safe_div(f.net_profit, f.equity),
        "operating_cf": f.operating_cf,
    }


def _endpoints(history: list[FinancialData], field: str) -> tuple[float, float]:
    """返回 (最近值, 最早值) — history 按报告期倒序。"""
    end = float(getattr(history[0], field))
    start = float(getattr(history[-1], field))
    return end, start


def _cagr(end: float, start: float, n: int | None = None) -> float:
    """计算 CAGR:start/end 间跨 history 长度的年化增长率。

    含负值时回退到线性对数近似,始终返回有限数值。
    """
    n = n if n is not None else 1
    if n <= 0:
        return 0.0
    try:
        if start == 0 or end == 0:
            return 0.0
        if (start > 0 and end > 0):
            ratio = end / start
            if ratio > 0:
                return ratio ** (1.0 / n) - 1.0
        # 负值/异号:用对数线性回退
        log_end = math.log(abs(end) if end != 0 else 1e-9)
        log_start = math.log(abs(start) if start != 0 else 1e-9)
        return math.exp((log_end - log_start) / n) - 1.0
    except (ValueError, ZeroDivisionError, OverflowError):
        return 0.0


def _safe_div(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return a / b


def _detect_break_points(history: list[FinancialData]) -> list[dict[str, object]]:
    """检测年际关键断点:营收/净利同比大幅下滑(>30pp)或符号翻转。"""
    breaks: list[dict[str, object]] = []
    for prev, cur in zip(history, history[1:]):
        if cur.revenue_yoy != 0.0 and prev.revenue_yoy - cur.revenue_yoy > 0.3:
            breaks.append(
                {
                    "type": "revenue_drop",
                    "period": cur.report_period,
                    "prev_yoy": prev.revenue_yoy,
                    "cur_yoy": cur.revenue_yoy,
                }
            )
        if (prev.net_profit > 0 and cur.net_profit < 0) or (
            prev.net_profit < 0 and cur.net_profit > 0
        ):
            breaks.append(
                {
                    "type": "profit_sign_flip",
                    "period": cur.report_period,
                    "prev_np": prev.net_profit,
                    "cur_np": cur.net_profit,
                }
            )
    return breaks
