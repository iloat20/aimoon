"""Tests for the unified data integrity checker (audit P3.1, priority 1).

Covers all 6 dimension checkers + divergence + the DataValidator port wrapper.
With Phase 6.1 the StockAnalysis data fields default to None, so the
checker must degrade gracefully (it does, via `if not x` guards).
"""


from aimoon.adapters.driven.validation.integrity_checker import (
    IntegrityDataValidator,
    check_data_integrity,
)
from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.entities.capital_flow import CapitalFlowData
from aimoon.core.domain.entities.financial import FinancialData
from aimoon.core.domain.entities.kline import KlineData
from aimoon.core.domain.entities.quote import StockQuote
from aimoon.core.domain.entities.social import SocialPost
from aimoon.core.domain.value_objects.kline_bar import KlineBar


def _bar(d: str, close: float = 10.5) -> KlineBar:
    return KlineBar(date=d, open=10.0, high=11.0, low=9.0, close=close, volume=1000)


# ---------------------------------------------------------------------------
# all-empty baseline
# ---------------------------------------------------------------------------
def test_all_empty_yields_low_confidence_everywhere():
    warnings, conf = check_data_integrity(StockAnalysis(symbol="600519"))
    assert conf == {"行情": "低", "K线数据": "低", "资金面": "低", "基本面": "低", "舆情数据": "低"}
    joined = "\n".join(warnings)
    assert "行情数据异常" in joined
    assert "K线数据为空" in joined
    assert "资金面数据全部获取失败" in joined
    assert "财务数据缺失" in joined
    assert "舆情数据为空" in joined


# ---------------------------------------------------------------------------
# quote
# ---------------------------------------------------------------------------
def test_quote_full_valid_scores_high():
    info = StockAnalysis(
        symbol="600519",
        quote=StockQuote(price=100, turnover=0.8, volume=1_000_000, pe=30, high=102, low=98),
    )
    warnings, conf = check_data_integrity(info)
    assert conf["行情"] == "高"
    assert not any("行情" in w for w in warnings)


def test_quote_price_zero_is_low():
    info = StockAnalysis(symbol="600519", quote=StockQuote(price=0, high=0, low=0))
    warnings, conf = check_data_integrity(info)
    assert conf["行情"] == "低"
    assert any("行情数据异常" in w for w in warnings)


def test_quote_volume_positive_turnover_zero_inconsistent():
    info = StockAnalysis(
        symbol="600519", quote=StockQuote(price=100, volume=1_000, turnover=0.0, high=101, low=99)
    )
    warnings, _ = check_data_integrity(info)
    assert any("成交量与换手率数据不一致" in w for w in warnings)


def test_quote_halted_warns():
    info = StockAnalysis(
        symbol="600519", quote=StockQuote(price=100, volume=0, turnover=0.0, high=101, low=99)
    )
    warnings, _ = check_data_integrity(info)
    assert any("可能为停牌状态" in w for w in warnings)


def test_quote_low_turnover_downgrades():
    info = StockAnalysis(
        symbol="600519", quote=StockQuote(price=100, turnover=0.1, volume=1, pe=30, high=101, low=99)  # noqa: E501
    )
    warnings, _ = check_data_integrity(info)
    assert any("交投清淡" in w for w in warnings)


# ---------------------------------------------------------------------------
# kline
# ---------------------------------------------------------------------------
def test_kline_ample_scores_medium():
    info = StockAnalysis(
        symbol="600519", kline=KlineData(bars=[_bar(f"2024-01-{i:02d}") for i in range(1, 65)], source="akshare")  # noqa: E501
    )
    _, conf = check_data_integrity(info)
    # n>=60 -> +1 over base 3 = 4 -> "中" (kline has no extra +point)
    assert conf["K线数据"] == "中"


def test_kline_small_sample_warns():
    info = StockAnalysis(symbol="600519", kline=KlineData(bars=[_bar("2024-01-01")], source="akshare"))  # noqa: E501
    warnings, conf = check_data_integrity(info)
    assert conf["K线数据"] == "低"
    assert any("K线样本仅1根" in w for w in warnings)


def test_kline_medium_sample_ok():
    info = StockAnalysis(
        symbol="600519", kline=KlineData(bars=[_bar(f"2024-01-{i:02d}") for i in range(1, 25)], source="akshare")  # noqa: E501
    )
    warnings, _ = check_data_integrity(info)
    assert not any("K线样本" in w for w in warnings)


def test_kline_tencent_source_warns():
    info = StockAnalysis(symbol="600519", kline=KlineData(bars=[_bar("2024-01-01")], source="tencent(fqkline)"))  # noqa: E501
    warnings, _ = check_data_integrity(info)
    assert any("腾讯降级源" in w for w in warnings)


def test_kline_empty_is_low():
    info = StockAnalysis(symbol="600519", kline=KlineData(bars=[]))
    warnings, conf = check_data_integrity(info)
    assert conf["K线数据"] == "低"
    assert any("K线数据为空" in w for w in warnings)


def test_kline_all_failed_is_low():
    info = StockAnalysis(symbol="600519", kline=KlineData(bars=[_bar("2024-01-01")], source="all_failed"))  # noqa: E501
    warnings, conf = check_data_integrity(info)
    assert conf["K线数据"] == "低"
    assert any("K线数据全部获取失败" in w for w in warnings)


# ---------------------------------------------------------------------------
# capital flow
# ---------------------------------------------------------------------------
def test_capital_flow_all_zero_is_low():
    info = StockAnalysis(symbol="600519", capital_flow=CapitalFlowData())
    warnings, conf = check_data_integrity(info)
    assert conf["资金面"] == "低"
    assert any("资金面数据全为零值" in w for w in warnings)


def test_capital_flow_valid_scores_high():
    info = StockAnalysis(
        symbol="600519",
        capital_flow=CapitalFlowData(
            main_net_5d=1e9, main_net_3d=5e8, northbound_chg=2e8, lhb_date="2024-01-01", source="akshare"  # noqa: E501
        ),
    )
    warnings, conf = check_data_integrity(info)
    assert conf["资金面"] == "高"
    assert any("akshare" in w for w in warnings)


# ---------------------------------------------------------------------------
# financial
# ---------------------------------------------------------------------------
def test_financial_missing_is_low():
    info = StockAnalysis(symbol="600519", financial=FinancialData())
    warnings, conf = check_data_integrity(info)
    assert conf["基本面"] == "低"
    assert any("财务数据缺失" in w for w in warnings)


def test_financial_valid_scores_high():
    info = StockAnalysis(
        symbol="600519",
        financial=FinancialData(report_period="2023年报", revenue=1e10, roe=20, eps=40),
    )
    _, conf = check_data_integrity(info)
    assert conf["基本面"] == "高"


# ---------------------------------------------------------------------------
# social
# ---------------------------------------------------------------------------
def test_social_empty_is_low():
    info = StockAnalysis(symbol="600519")
    warnings, conf = check_data_integrity(info)
    assert conf["舆情数据"] == "低"
    assert any("舆情数据为空" in w for w in warnings)


def test_social_few_samples_downgrades():
    info = StockAnalysis(
        symbol="600519",
        social_posts=tuple(SocialPost(platform="guba", url=f"u{i}") for i in range(5)),
    )
    warnings, conf = check_data_integrity(info)
    assert conf["舆情数据"] == "低"
    assert any("舆情样本仅5条" in w for w in warnings)


def test_social_medium_ok():
    info = StockAnalysis(
        symbol="600519",
        social_posts=tuple(SocialPost(platform="guba", url=f"u{i}") for i in range(12)),
    )
    warnings, _ = check_data_integrity(info)
    assert not any("舆情样本" in w for w in warnings)


def test_social_ample_scores_high():
    info = StockAnalysis(
        symbol="600519",
        social_posts=tuple(SocialPost(platform="guba", url=f"u{i}") for i in range(35)),
    )
    _, conf = check_data_integrity(info)
    assert conf["舆情数据"] == "中"


# ---------------------------------------------------------------------------
# divergence
# ---------------------------------------------------------------------------
def test_divergence_price_up_with_outflow():
    info = StockAnalysis(
        symbol="600519",
        quote=StockQuote(price=100, change_pct=2.0, high=101, low=99),
        capital_flow=CapitalFlowData(main_net_5d=-2e8, source="akshare"),
    )
    warnings, _ = check_data_integrity(info)
    assert any("量价背离" in w for w in warnings)


def test_divergence_price_down_with_inflow():
    info = StockAnalysis(
        symbol="600519",
        quote=StockQuote(price=100, change_pct=-2.0, high=101, low=99),
        capital_flow=CapitalFlowData(main_net_5d=2e8, source="akshare"),
    )
    warnings, _ = check_data_integrity(info)
    assert any("量价背离" in w for w in warnings)


def test_no_divergence_when_aligned():
    info = StockAnalysis(
        symbol="600519",
        quote=StockQuote(price=100, change_pct=2.0, high=101, low=99),
        capital_flow=CapitalFlowData(main_net_5d=2e8, source="akshare"),
    )
    warnings, _ = check_data_integrity(info)
    assert not any("量价背离" in w for w in warnings)


# ---------------------------------------------------------------------------
# DataValidator port wrapper
# ---------------------------------------------------------------------------
def test_validator_class_wraps_function():
    validator = IntegrityDataValidator()
    info = StockAnalysis(
        symbol="600519",
        quote=StockQuote(price=100, turnover=0.8, volume=1_000_000, pe=30, high=101, low=99),
    )
    warnings, conf = validator.validate(info)
    assert conf["行情"] == "高"
    assert isinstance(warnings, list)
