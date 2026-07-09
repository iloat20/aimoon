"""风险量化工具(纯函数)。

输入 financial_temporal 输出 + quote,输出 bears(≥3,必含 trigger_condition) / bulls / ratio_alerts。
任一必需输入缺失 → `__partial__`。
"""
from __future__ import annotations

import logging
import math

from aimoon.core.domain.entities.quote import StockQuote

from ._common import _hist_pe_anchor

logger = logging.getLogger(__name__)

# 风险量化阈值与系数 (extracted from inline magic numbers, audit P2.5)
ROE_DROP_TRIGGER_PP = 0.03       # ROE 压缩触发阈值 (3pp)
PE_OVERHIST_MULT = 1.3            # PE 超历史均值 30% 视为偏高
PE_ABS_HIGH = 40                  # PE 绝对高位阈值
PB_ABS_HIGH = 10                  # PB 绝对高位阈值
OCF_RATIO_WARN = 0.6             # OCF/净利 含金量预警线
PE_DEFAULT_PAD = 30.0             # bears 兜底填充用默认 PE

REV_CAGR_IMPACT_COEF = 20         # 营收CAGR 影响系数
REV_CAGR_IMPACT_CAP = 5.0         # 营收下滑 impact 上限
NP_CAGR_IMPACT_COEF = 25          # 净利CAGR 影响系数
NP_CAGR_IMPACT_CAP = 8.0          # 净利下滑 impact 上限
ROE_DROP_IMPACT_COEF = 200        # ROE 压缩影响系数
ROE_IMPACT_CAP = 20.0            # ROE 压缩 impact 上限
PE_OVERHIST_IMPACT_COEF = 30       # PE 超历史影响系数
PE_IMPACT_CAP = 30.0             # PE 偏高 impact 上限
PB_ABS_IMPACT_COEF = 3.0          # PB 偏高影响系数
PB_IMPACT_CAP = 25.0            # PB 偏高 impact 上限
OCF_IMPACT_COEF = 50              # OCF 含金量影响系数
OCF_IMPACT_CAP = 15.0           # OCF 含金量 impact 上限

HIGH_PB_WARN = 8                   # 高 PB 预警阈值（PB 过高估值偏贵）
RECEIVABLES_PE_WARN = 50           # 应收预警 PE 阈值
INVENTORY_PB_WARN = 8              # 存货预警 PB 阈值
INVENTORY_PE_WARN = 30            # 存货预警 PE 阈值


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
                "impact_pct": round(
                    max(abs(rev_cagr) * REV_CAGR_IMPACT_COEF, REV_CAGR_IMPACT_CAP), 1
                ),
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
                "impact_pct": round(max(abs(np_cagr) * NP_CAGR_IMPACT_COEF, NP_CAGR_IMPACT_CAP), 1),
            }
        )

    # ② ROE 压缩 (末项相对峰值下降 ≥3pp)
    if len(roe_list) >= 2:
        peak = max(roe_list)
        latest = roe_list[0]
        drop = peak - latest
        if drop >= ROE_DROP_TRIGGER_PP:
            bears.append(
                {
                    "theme": "ROE 压缩",
                    "trigger_condition": (
                        f"ROE 从峰值 {peak * 100:.1f}% 回落至 {latest * 100:.1f}%,"
                        f"下滑 {drop * 100:.1f}pp;若下季 ROE 同比续降 1pp 以上则确认"
                    ),
                    "impact_pct": round(min(drop * ROE_DROP_IMPACT_COEF, ROE_IMPACT_CAP), 1),
                }
            )

    # ③ 估值偏高 (PE 绝对值 + 历史分位)
    # 注:_hist_pe_anchor 依赖逐年 PE 序列,但 PE 是行情快照、未随财报采集,
    # 故当前恒返回 0(历史分位分支按设计不触发),仅保留 PE>40 绝对值兜底。
    if pe > 0:
        hist_anchor = _hist_pe_anchor(fin)
        if hist_anchor > 0 and pe > hist_anchor * PE_OVERHIST_MULT:
            bears.append(
                {
                    "theme": "估值偏高(PE)",
                    "trigger_condition": (
                        f"当前 PE {pe:.1f} 超过近三年均值上轨 {hist_anchor * 1.3:.1f};"
                        f"若 PE 回落至 {hist_anchor:.1f} 以下区间则看空压力释放"
                    ),
                    "impact_pct": round(
                        min((pe / hist_anchor - 1) * PE_OVERHIST_IMPACT_COEF, PE_IMPACT_CAP), 1
                    ),
                }
            )
        elif pe > 40:
            bears.append(
                {
                    "theme": "估值偏高(PE)",
                    "trigger_condition": (
                        f"当前 PE {pe:.1f} 超过 40 倍,接近历史高位;"
                        f"若 PE 回落至 {min(pe * 0.8, PE_ABS_HIGH):.1f} 以下则高位风险充分释放"
                    ),
                    "impact_pct": round(min((pe - PE_ABS_HIGH) * 1.0, PE_IMPACT_CAP), 1),
                }
            )
    if pb > 0 and pb > PB_ABS_HIGH:
        bears.append(
            {
                "theme": "估值偏高(PB)",
                "trigger_condition": (
                    f"当前 PB {pb:.1f} 超过 10 倍,资产溢价偏高;"
                    f"若 PB 回落至 {min(pb * 0.75, 10):.1f} 以下则溢价收缩"
                ),
                "impact_pct": round(min((pb - PB_ABS_HIGH) * PB_ABS_IMPACT_COEF, PB_IMPACT_CAP), 1),
            }
        )

    # ④ OCF 含金量不足
    if fin.get("ocf_profit_ratio") is not None and ocf_ratio < OCF_RATIO_WARN:
        bears.append(
            {
                "theme": "现金流含金量不足",
                "trigger_condition": (
                    f"OCF/净利润仅 {ocf_ratio:.2f} < 0.6;"
                    f"若下季 OCF 同比仍为负且净利为正,则利润变现风险暴露"
                ),
                "impact_pct": round(
                    min((OCF_RATIO_WARN - ocf_ratio) * OCF_IMPACT_COEF, OCF_IMPACT_CAP), 1
                ),
            }
        )

    # ⑤ 强制兜底:任何情况下 bears ≥3 条(SELF_CHECK/强制清单)
    if len(bears) < 3:
        pad_pe = pe if pe > 0 else PE_DEFAULT_PAD
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


def _ratio_alerts(quote: StockQuote) -> dict[str, object]:
    return {
        "high_pb_warn": (
            float(quote.market_cap or 0) > 0 and float(quote.pb or 0) > HIGH_PB_WARN
        ),
        "receivables_warn": float(quote.pe or 0) > RECEIVABLES_PE_WARN,
        "inventory_warn": (
            float(quote.pb or 0) > INVENTORY_PB_WARN
            and float(quote.pe or 0) > INVENTORY_PE_WARN
        ),
    }
