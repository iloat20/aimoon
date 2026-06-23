"""Unified data integrity checker — cross-validates all collected data.

Produces data_warnings (shown in report) and per-dimension confidence levels.
"""

from __future__ import annotations

from ..models.stock import StockInfo


def check_data_integrity(info: StockInfo) -> tuple[list[str], dict[str, str]]:
    """Run all validations, return (warnings, dimension_confidence).

    dimension_confidence maps dimension names to "高"/"中"/"低".
    """
    warnings: list[str] = []
    confidence: dict[str, str] = {}

    _check_quote(info, warnings, confidence)
    _check_kline(info, warnings, confidence)
    _check_capital_flow(info, warnings, confidence)
    _check_financial(info, warnings, confidence)
    _check_social(info, warnings, confidence)
    _check_divergence(info, warnings)

    return warnings, confidence


def _check_quote(
    info: StockInfo, warnings: list[str], confidence: dict[str, str]
) -> None:
    q = info.quote
    if not q or q.price <= 0:
        confidence["行情"] = "低"
        warnings.append("行情数据异常（价格为零或负值），数据不可靠")
        return

    score = 3
    if q.turnover >= 0.5:
        score += 1
    if q.volume > 0:
        score += 1
    if q.pe > 0:
        score += 1
    if q.high > q.low > 0:
        score += 1

    # Volume vs turnover consistency
    if q.volume > 0 and q.turnover == 0:
        warnings.append("成交量与换手率数据不一致（成交量为正但换手率为0）")
        score -= 1
    elif q.turnover == 0 and q.volume == 0:
        score -= 1

    if q.turnover < 0.5 and q.turnover > 0:
        warnings.append(
            f"换手率仅{q.turnover}%，交投清淡，该结论基于有限数据，仅供参考"
        )
        score -= 1

    confidence["行情"] = _score_to_level(score)


def _check_kline(
    info: StockInfo, warnings: list[str], confidence: dict[str, str]
) -> None:
    k = info.kline
    if not k or not k.bars:
        confidence["技术面"] = "低"
        warnings.append("K线数据为空，技术面分析不可用")
        return

    score = 3
    n = len(k.bars)

    if n >= 60:
        score += 1
    elif n >= 20:
        pass
    else:
        warnings.append(f"K线样本仅{n}根，技术指标计算精度有限")
        score -= 1

    if k.source in ("tencent(fqkline)",):
        warnings.append("K线数据来自腾讯降级源，技术指标精度可能受限")
        score -= 1
    elif k.source == "all_failed":
        warnings.append("K线数据全部获取失败")
        score = 1

    # Support/resistance sanity
    if n >= 5:
        from ..indicators.technical import compute_indicators

        ind = compute_indicators(k)
        price = ind.get("price", 0)
        support = ind.get("support", 0)
        resistance = ind.get("resistance", 0)
        if price and support and resistance:
            if support >= price:
                warnings.append(
                    f"支撑位({support})高于当前价({price})，数据可能存在异常"
                )
                score -= 1
            if resistance <= price:
                warnings.append(
                    f"阻力位({resistance})低于当前价({price})，数据可能存在异常"
                )
                score -= 1
            if support >= resistance:
                warnings.append("支撑位高于阻力位，技术指标数据异常")
                score -= 1

    confidence["技术面"] = _score_to_level(score)


def _check_capital_flow(
    info: StockInfo, warnings: list[str], confidence: dict[str, str]
) -> None:
    cf = info.capital_flow
    if not cf or cf.source == "all_failed":
        confidence["资金面"] = "低"
        warnings.append("资金面数据全部获取失败")
        return

    score = 3
    if cf.main_net_5d != 0:
        score += 1
    if cf.net_3d != 0:
        score += 1
    if cf.northbound_chg != 0:
        score += 1
    if cf.lhb_date:
        score += 1

    source = cf.source
    if "pysnowball" in source:
        warnings.append(f"资金流向数据来自{source}，数据精度可能有限")
        score -= 1
    elif "ths" in source:
        pass

    confidence["资金面"] = _score_to_level(score)


def _check_financial(
    info: StockInfo, warnings: list[str], confidence: dict[str, str]
) -> None:
    f = info.financial
    if not f or not f.report_period:
        confidence["基本面"] = "低"
        warnings.append("财务数据缺失（未配置XUEQIU_TOKEN），基本面分析不完整")
        return

    score = 3
    if f.revenue > 0:
        score += 1
    if f.roe > 0:
        score += 1
    if f.eps > 0:
        score += 1

    confidence["基本面"] = _score_to_level(score)


def _check_social(
    info: StockInfo, warnings: list[str], confidence: dict[str, str]
) -> None:
    n = len(info.social_posts)
    if n == 0:
        confidence["市场情绪"] = "低"
        warnings.append("舆情数据为空，情绪分析不可用")
        return

    score = 3
    if n >= 30:
        score += 1
    elif n >= 10:
        pass
    elif n >= 5:
        warnings.append(f"舆情样本仅{n}条，情绪分析代表性有限，仅供参考")
        score -= 1
    else:
        warnings.append(f"舆情样本仅{n}条，情绪分析参考价值极低")
        score = 1

    confidence["市场情绪"] = _score_to_level(score)


def _check_divergence(info: StockInfo, warnings: list[str]) -> None:
    """Check price vs capital flow divergence."""
    cf = info.capital_flow
    q = info.quote
    if not cf or not q or cf.source == "all_failed":
        return
    if q.price <= 0:
        return

    big_net = cf.net_3d
    change = q.change_pct

    if big_net > 1e8 and change < -1:
        warnings.append(
            f"近3日净流入{big_net / 1e8:.2f}亿，但股价下跌{change}%，出现量价背离"
        )
    elif big_net < -1e8 and change > 1:
        warnings.append(
            f"近3日净流出{abs(big_net) / 1e8:.2f}亿，"
            f"但股价上涨{change}%，出现量价背离"
        )


def _score_to_level(s: int) -> str:
    if s >= 5:
        return "高"
    if s >= 3:
        return "中"
    return "低"
