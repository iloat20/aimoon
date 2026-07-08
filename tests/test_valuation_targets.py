"""估值三档表修复回归测试。

修复点:
1. 单位:返回「每股目标价(元)」而非「股权总价值(元)」(旧实现把总价值误标为每股价)。
2. 乐观档不再爆炸:永续增速封顶(< 折现率),避免 Gordon 终值系数趋于无穷。
3. 透明显性:每档给目标价 + PE,概率列为 N/A(模型不估概率,不假装)。
"""
from __future__ import annotations

from aimoon.adapters.driven.ai.pipeline.table_renderer import render_valuation_targets
from aimoon.adapters.driven.ai.tools.valuation import run as val_run


class _Quote:
    pe = 20.0
    pb = 8.0
    market_cap = 1.7e12  # 1.7 万亿
    price = 1400.0


def _fin(cagr: float = 0.07):
    return {
        "years": [
            {"period": "2021", "operating_cf": 6.0e10, "investing_cf": -3e9},
            {"period": "2022", "operating_cf": 6.7e10, "investing_cf": -3e9},
            {"period": "2023", "operating_cf": 6.6e10, "investing_cf": -3e9},
        ],
        "revenue_cagr": cagr,
        "ocf_partial": False,
    }


def test_per_share_price_not_total_equity_value():
    out = val_run(_fin(), _Quote(), None)
    targets = out["fcfe_targets"]
    neutral = targets["neutral"]
    # 必须是 dict(含 price/pe),而非裸总价值浮点
    assert isinstance(neutral, dict)
    price = float(neutral["price"])
    # 每股目标价应在合理 A 股区间(几百~几千元),绝不是 1.9 万亿那样的总价值量级
    assert 100.0 < price < 5000.0, f"neutral 每股目标价异常: {price}"
    # 应远小于「总价值误标为每股价」时的 ~19000
    assert price < 5000.0


def test_optimistic_does_not_explode_on_high_growth():
    # growth=0.12 远超 discount 0.10,旧实现乐观档会爆炸到 ~5.7e13
    out = val_run(_fin(cagr=0.12), _Quote(), None)
    targets = out["fcfe_targets"]
    for tier in ("conservative", "neutral", "optimistic"):
        t = targets[tier]
        price = float(t["price"])
        # 任何档位都不应出现天文数字(总价值量级的误标/爆炸)
        assert price < 5000.0, f"{tier} 目标价爆炸: {price}"
    # 乐观档应 >= 中性档(更高增长假设),且差距有限
    assert targets["optimistic"]["price"] >= targets["neutral"]["price"]


def test_probability_is_na_not_fabricated():
    out = val_run(_fin(), _Quote(), None)
    for tier in ("conservative", "neutral", "optimistic"):
        assert out["fcfe_targets"][tier]["probability"] is None


def test_terminal_growth_capped_in_assumptions():
    out = val_run(_fin(), _Quote(), None)
    a = out["fcfe_assumptions"]
    assert "terminal_growth" in a
    # 永续增速封顶: <= 2.5% 且严格低于折现率 10%
    assert 0.0 <= a["terminal_growth"] <= 0.025
    assert a["terminal_growth"] < a["discount_rate"]


def test_render_shows_per_share_target_with_pe():
    out = val_run(_fin(), _Quote(), None)
    md = render_valuation_targets(out)
    assert "## 估值三档表" in md
    assert "| 档位 | PE | 目标价(元) | 概率(%) |" in md
    assert "N/A" in md  # 概率列显式标 N/A
    # 假设行应以百分比显示(10.0% / 2.5%),而非误读为 0.1
    assert "折现率=10.0%" in md
    assert "永续增速封顶=2.5%" in md
