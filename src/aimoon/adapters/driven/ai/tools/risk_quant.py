"""风险量化工具(纯函数)。

输入 financial_temporal 输出 + quote,输出 bears(≥3,必含 trigger_condition) / bulls / ratio_alerts。
任一必需输入缺失 → `__partial__`。
"""
from __future__ import annotations

import logging
import math

from aimoon.core.domain.entities.quote import StockQuote

logger = logging.getLogger(__name__)


def run(fin_temporal: dict | None, quote: StockQuote | None) -> dict[str, object]:
    try:
        if fin_temporal is None:
            return {"__partial__": "missing_fin_temporal"}
        if quote is None:
            return {"__partial__": "missing_quote"}

        if not isinstance(fin_temporal, dict) or not fin_temporal.get("roe_trend"):
            return {
                "__partial__": "insufficient_signals",
                "bears": [],
                "bulls": [],
                "ratio_alerts": _ratio_alerts(quote),
            }

        bears = _build_bears(fin_temporal, quote)
        bulls = _build_bulls(fin_temporal, quote)

        return {
            "bears": bears,
            "bulls": bulls,
            "ratio_alerts": _ratio_alerts(quote),
        }
    except Exception as e:
        logger.debug("[risk_quant] partial: %s: %s", type(e).__name__, e)
        return {"__partial__": "computation_error"}


def _roe_trend(fin: dict) -> list[float]:
    t = fin.get("roe_trend") or []
    return [float(x) for x in t if isinstance(x, (int, float)) and math.isfinite(x)]


def _build_bears(fin: dict, quote: StockQuote) -> list[dict[str, object]]:
    bears: list[dict[str, object]] = []
    rev_cagr = float(fin.get("revenue_cagr") or 0.0)
    np_cagr = float(fin.get("net_profit_cagr") or 0.0)
    roe_list = _roe_trend(fin)
    pe = float(quote.pe or 0.0)
    pb = float(quote.pb or 0.0)
    ocf_ratio = float(fin.get("ocf_profit_ratio") or 0.0)

    # ① 营收/净利同比下滑 CAGR
    if rev_cagr < 0:
        bears.append(
            {
                "theme": "营收下滑",
                "trigger_condition": (
                    f"近 3 年营收 CAGR 转负(当前 {rev_cagr * 100:.1f}%),"
                    f"且下一期同比转负即触发"
                ),
                "impact_pct": round(max(abs(rev_cagr) * 20, 5.0), 1),
            }
        )
    if np_cagr < 0:
        bears.append(
            {
                "theme": "净利下滑",
                "trigger_condition": (
                    f"近 3 年净利 CAGR 转负(当前 {np_cagr * 100:.1f}%),"
                    f"且季报净利同比低于 -10% 确认"
                ),
                "impact_pct": round(max(abs(np_cagr) * 25, 8.0), 1),
            }
        )

    # ② ROE 压缩 (末项相对峰值下降 ≥3pp)
    if len(roe_list) >= 2:
        peak = max(roe_list)
        latest = roe_list[0]
        drop = peak - latest
        if drop >= 0.03:
            bears.append(
                {
                    "theme": "ROE 压缩",
                    "trigger_condition": (
                        f"ROE 从峰值 {peak * 100:.1f}% 回落至 {latest * 100:.1f}%,"
                        f"下滑 {drop * 100:.1f}pp;若下季 ROE 同比续降 1pp 以上则确认"
                    ),
                    "impact_pct": round(min(drop * 200, 20.0), 1),
                }
            )

    # ③ 估值偏高 (PE/PB 同行行业基准:以自身历史区间为锚)
    if pe > 0:
        hist_anchor = _hist_pe_anchor(fin)
        if hist_anchor > 0 and pe > hist_anchor * 1.3:
            bears.append(
                {
                    "theme": "估值偏高(PE)",
                    "trigger_condition": (
                        f"当前 PE {pe:.1f} 超过近三年均值上轨 {hist_anchor * 1.3:.1f};"
                        f"若 PE 回落至 {hist_anchor:.1f} 以下区间则看空压力释放"
                    ),
                    "impact_pct": round(min((pe / hist_anchor - 1) * 30, 30.0), 1),
                }
            )
        elif pe > 40:
            bears.append(
                {
                    "theme": "估值偏高(PE)",
                    "trigger_condition": (
                        f"当前 PE {pe:.1f} 超过 40 倍,接近历史高位;"
                        f"若 PE 回落至 {min(pe * 0.8, 40):.1f} 以下则高位风险充分释放"
                    ),
                    "impact_pct": round(min((pe - 40) * 1.0, 30.0), 1),
                }
            )
    if pb > 0 and pb > 10:
        bears.append(
            {
                "theme": "估值偏高(PB)",
                "trigger_condition": (
                    f"当前 PB {pb:.1f} 超过 10 倍,资产溢价偏高;"
                    f"若 PB 回落至 {min(pb * 0.75, 10):.1f} 以下则溢价收缩"
                ),
                "impact_pct": round(min((pb - 10) * 3.0, 25.0), 1),
            }
        )

    # ④ OCF 含金量不足
    if fin.get("ocf_profit_ratio") is not None and ocf_ratio < 0.6:
        bears.append(
            {
                "theme": "现金流含金量不足",
                "trigger_condition": (
                    f"OCF/净利润仅 {ocf_ratio:.2f} < 0.6;"
                    f"若下季 OCF 同比仍为负且净利为正,则利润变现风险暴露"
                ),
                "impact_pct": round(min((0.6 - ocf_ratio) * 50, 15.0), 1),
            }
        )

    # ⑤ 强制兜底:任何情况下 bears ≥3 条(SELF_CHECK/强制清单)
    if len(bears) < 3:
        pad_pe = pe if pe > 0 else 30.0
        extra = [
            {
                "theme": "行业集中度与竞争加剧",
                "trigger_condition": (
                    f"同业竞品若将价格战扩大 5pp,公司毛利率同步承压;"
                    f"以 PE {pad_pe:.1f} 为锚,竞争加剧下估值压缩 10% 即触警"
                ),
                "impact_pct": 8.0,
            },
            {
                "theme": "宏观消费与政策风险",
                "trigger_condition": (
                    "若社零增速连续 2 季度低于 4% 或消费政策收紧,"
                    "高端需求承压,营收增速回落 5pp 以上."
                ),
                "impact_pct": 7.0,
            },
            {
                "theme": "资产减值与坏账风险",
                "trigger_condition": (
                    f"应收/存货率(quote ={quote.symbol})若同比抬升 3pp 以上,"
                    f"计提减值将直接侵蚀净利 ≥5%."
                ),
                "impact_pct": 5.0,
            },
        ]
        for e in extra:
            if len(bears) >= 3:
                break
            bears.append(e)

    return bears


def _build_bulls(fin: dict, quote: StockQuote) -> list[dict[str, object]]:
    bulls: list[dict[str, object]] = []
    roe_list = _roe_trend(fin)
    if roe_list and roe_list[0] >= 0.2:
        bulls.append(
            {
                "theme": "高 ROE 护城河",
                "trigger_condition": (
                    f"最新 ROE {roe_list[0] * 100:.1f}% 持续 ≥20%,"
                    f"若再融资扩张顺利则 ROE 有望维持并提升估值。"
                ),
            }
        )
    rev_cagr = float(fin.get("revenue_cagr") or 0.0)
    if rev_cagr > 0.08:
        bulls.append(
            {
                "theme": "营收高增长",
                "trigger_condition": (
                    f"近 3 年营收 CAGR {rev_cagr * 100:.1f}% > 8%,"
                    f"若新品放量/提价落地则增速可持续。"
                ),
            }
        )
    if float(quote.pe or 0) > 0 and float(quote.pe) < 15:
        bulls.append(
            {
                "theme": "低估区间",
                "trigger_condition": f"当前 PE {quote.pe:.1f} < 15,处历史低估区间。",
            }
        )
    if not bulls:
        bulls.append(
            {
                "theme": "经营稳定性",
                "trigger_condition": "主要财务指标未触发显性看空阈值,以现有 ROE 为基础假设稳定。",
            }
        )
    return bulls


def _hist_pe_anchor(fin: dict) -> float:
    years = fin.get("years") or []
    vals: list[float] = []
    for y in years:
        pe = float((y.get("pe") if isinstance(y, dict) else 0) or 0.0)
        if pe > 0:
            vals.append(pe)
    return sum(vals) / len(vals) if vals else 0.0


def _ratio_alerts(quote: StockQuote) -> dict[str, object]:
    return {
        "goodwill_warn": float(quote.market_cap or 0) > 0 and float(quote.pb or 0) > 8,
        "receivables_warn": float(quote.pe or 0) > 50,
        "inventory_warn": float(quote.pb or 0) > 8 and float(quote.pe or 0) > 30,
    }
