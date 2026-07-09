"""Unified data integrity checker — cross-validates all collected data.

Produces data_warnings (shown in report) and per-dimension confidence levels.
"""

from __future__ import annotations

from aimoon.core.application.ports import DataValidator
from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis


def check_data_integrity(info: StockAnalysis) -> tuple[list[str], dict[str, str]]:
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


def _check_quote(info: StockAnalysis, warnings: list[str], confidence: dict[str, str]) -> None:
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

    if q.volume > 0 and q.turnover == 0:
        warnings.append("成交量与换手率数据不一致（成交量为正但换手率为0）")
        score -= 2
    elif q.turnover == 0 and q.volume == 0:
        warnings.append("成交量与换手率均为0，可能为停牌状态")
        score -= 1

    if q.turnover < 0.5 and q.turnover > 0:
        warnings.append(f"换手率仅{q.turnover}%，交投清淡，该结论基于有限数据，仅供参考")
        score -= 1

    confidence["行情"] = _score_to_level(score)


def _check_kline(info: StockAnalysis, warnings: list[str], confidence: dict[str, str]) -> None:
    k = info.kline
    if not k or not k.bars or k.source == "all_failed":
        confidence["K线数据"] = "低"
        if k and k.source == "all_failed":
            warnings.append("K线数据全部获取失败")
        else:
            warnings.append("K线数据为空")
        return

    score = 3
    n = len(k.bars)

    if n >= 60:
        score += 1
    elif n >= 20:
        pass
    else:
        warnings.append(f"K线样本仅{n}根，数据样本有限")
        score -= 1

    if k.source in ("tencent(fqkline)", "eastmoney(direct)"):
        warnings.append("K线数据来自降级源，数据精度可能受限")
        score -= 1

    confidence["K线数据"] = _score_to_level(score)


def _check_capital_flow(
    info: StockAnalysis, warnings: list[str], confidence: dict[str, str]
) -> None:
    cf = info.capital_flow
    if not cf or cf.source == "all_failed":
        confidence["资金面"] = "低"
        warnings.append("资金面数据全部获取失败")
        return

    score = 3
    if cf.main_net_5d != 0:
        score += 1
    if cf.main_net_3d != 0:
        score += 1
    if cf.northbound_chg != 0:
        score += 1
    if cf.lhb_date:
        score += 1

    if (
        cf.main_net_5d == 0
        and cf.main_net_3d == 0
        and cf.main_net_10d == 0
        and cf.main_net_20d == 0
        and cf.northbound_chg == 0
        and not cf.lhb_date
    ):
        confidence["资金面"] = "低"
        warnings.append("资金面数据全为零值，可能未正确获取")
        return

    source = cf.source
    if "akshare" in source or "eastmoney" in source:
        warnings.append(f"资金流向数据来自{source}，数据精度可能有限")
        score -= 1

    confidence["资金面"] = _score_to_level(score)


def _check_financial(info: StockAnalysis, warnings: list[str], confidence: dict[str, str]) -> None:
    f = info.financial
    if not f or not f.report_period:
        confidence["基本面"] = "低"
        warnings.append("财务数据缺失，基本面分析不完整")
        return

    score = 3
    if f.revenue > 0:
        score += 1
    if f.roe > 0:
        score += 1
    if f.eps > 0:
        score += 1

    confidence["基本面"] = _score_to_level(score)


def _check_social(info: StockAnalysis, warnings: list[str], confidence: dict[str, str]) -> None:
    n = len(info.social_posts)
    if n == 0:
        confidence["舆情数据"] = "低"
        warnings.append("舆情数据为空")
        return

    score = 3
    if n >= 30:
        score += 1
    elif n >= 10:
        pass
    elif n >= 5:
        warnings.append(f"舆情样本仅{n}条，数据代表性有限，仅供参考")
        score -= 1
    else:
        warnings.append(f"舆情样本仅{n}条，数据参考价值较低")
        score = 1

    confidence["舆情数据"] = _score_to_level(score)


def _check_divergence(info: StockAnalysis, warnings: list[str]) -> None:
    """Check price vs capital flow divergence."""
    cf = info.capital_flow
    q = info.quote
    if not cf or not q or cf.source == "all_failed":
        return
    if q.price <= 0:
        return

    main_net_5d = cf.main_net_5d
    change = q.change_pct

    if main_net_5d > 1e8 and change < -1:
        warnings.append(
            f"近5日主力净流入{main_net_5d / 1e8:.2f}亿，但股价下跌{change}%，出现量价背离"
        )
    elif main_net_5d < -1e8 and change > 1:
        warnings.append(
            f"近5日主力净流出{abs(main_net_5d) / 1e8:.2f}亿，但股价上涨{change}%，出现量价背离"
        )


def _score_to_level(s: int) -> str:
    if s >= 5:
        return "高"
    if s >= 3:
        return "中"
    return "低"


class IntegrityDataValidator(DataValidator):
    """数据完整性验证器 — 实现 DataValidator 端口接口。"""

    def validate(self, stock_info: StockAnalysis) -> tuple[list[str], dict[str, str]]:
        """验证股票数据的完整性和一致性。

        Args:
            stock_info: 待验证的股票信息领域实体

        Returns:
            (warnings, confidence) - 警告列表和各维度置信度
        """
        return check_data_integrity(stock_info)
