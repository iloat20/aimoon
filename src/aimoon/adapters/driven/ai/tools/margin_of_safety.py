"""估值安全边际工具(纯函数,零 LLM token)。

替代原 valuation 工具的三档目标价计算。本报告不输出任何目标价;本工具
确定性地计算「估值安全边际」指标,供【估值安全边际表】渲染、AI 直接引用:

- pe / pb           : 行情派生
- net_cash_pe       : (市值 - 货币资金) / 净利润  —— 剔除账面现金后的经营资产 PE
- peer_pe_median    : 同业 PE 中位数(来自 peer_compare)
- stress            : 确定性压力测试(净利 -30% / -50% → EPS → 股价 → 下行空间)

所有数字确定性计算,AI 只引用不重算,从根本上杜绝「目标价串号 / 压力测试抄错」
类一级 BUG。缺失数据对应项置 None → 渲染 N/A。
"""
from __future__ import annotations

import logging
import statistics

from aimoon.core.domain.entities.quote import StockQuote

logger = logging.getLogger(__name__)

# 压力测试情景:净利相对当前的跌幅(绝对值)。
STRESS_DROPS = (0.30, 0.50)


def _median(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None and v > 0]
    if not vals:
        return None
    return statistics.median(vals)


def run(
    fin_temporal: dict | None,
    quote: StockQuote | None,
    peer_comp: dict | None,
    financial: object | None = None,
) -> dict[str, object]:
    try:
        if quote is None:
            return {"__partial__": "missing_quote"}

        pe = float(quote.pe) if (quote.pe and quote.pe > 0) else 0.0
        pb = float(quote.pb or 0.0)
        price = float(quote.price or 0.0)
        market_cap = float(quote.market_cap or 0.0)

        # 总股本代理:市值 / 现价(与旧 valuation 工具一致,流通股本口径)。
        shares = (market_cap / price) if price > 0 else 0.0

        net_profit = (
            float(getattr(financial, "net_profit", 0.0) or 0.0) if financial else 0.0
        )
        monetary_funds = (
            float(getattr(financial, "monetary_funds", 0.0) or 0.0) if financial else 0.0
        )

        # 净现金调整 PE = (市值 - 货币资金) / 净利润
        # 货币资金缺失(默认 0.0 = 未采集)时不得计算:不能把"未知现金"当 0 减,
        # 否则会伪装出偏低的经营资产 PE。缺失 → None(渲染 N/A)。
        net_cash_pe: float | None = None
        if market_cap > 0 and net_profit > 0 and monetary_funds > 0:
            nc = market_cap - monetary_funds
            if nc > 0:
                net_cash_pe = nc / net_profit

        # 同业 PE 中位数(仅 peers,不含自身)
        peer_pe_median: float | None = None
        if isinstance(peer_comp, dict):
            peers = peer_comp.get("peers") or []
            peer_pes = [float(p.get("pe") or 0.0) for p in peers if isinstance(p, dict)]
            peer_pe_median = _median(peer_pes)

        # 确定性压力测试:净利跌 X% → EPS → 股价(恒定 PE)→ 下行空间
        stress: list[dict[str, object]] = []
        if net_profit > 0 and shares > 0 and pe > 0 and price > 0:
            for drop in STRESS_DROPS:
                s_np = net_profit * (1.0 - drop)
                s_eps = s_np / shares
                s_price = s_eps * pe
                downside = (s_price - price) / price if price > 0 else 0.0
                stress.append(
                    {
                        "drop": round(drop * 100, 1),
                        "net_profit": round(s_np / 1e8, 2),  # 亿元
                        "eps": round(s_eps, 2),
                        "price": round(s_price, 2),
                        "downside_pct": round(downside * 100, 1),
                    }
                )

        return {
            "pe": pe,
            "pb": pb,
            "net_cash_pe": round(net_cash_pe, 2) if net_cash_pe is not None else None,
            "peer_pe_median": peer_pe_median,
            "stress": stress,
        }
    except Exception as e:  # noqa: BLE001 - 安全降级,绝不抛到上游
        logger.debug("[margin_of_safety] partial: %s: %s", type(e).__name__, e)
        return {"__partial__": "computation_error"}
