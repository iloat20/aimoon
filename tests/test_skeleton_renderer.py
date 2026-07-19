"""Tests for skeleton-to-Markdown renderer (degraded/fast mode output)."""
from __future__ import annotations

from aimoon.adapters.driven.ai.pipeline.skeleton_renderer import render_skeleton_md


def _valid():
    return {
        "narratives": {
            "macro": {
                "probability": 0.7, "consensus": "地产下行",
                "our_view": "企稳", "falsify": "利率>3.5%->-8%",
            },
            "industry": {
                "probability": 0.8, "consensus": "价格战",
                "our_view": "趋缓", "falsify": "持续>6月->-12%",
            },
            "alpha": {
                "probability": 0.75, "consensus": "稳定",
                "our_view": "改善", "falsify": "管理层变动->-15%",
            },
        },
        "composite_prob": 0.42,
        "forensic_audit": {
            "items": [{"item": "OCF/利润", "status": "正常", "detail": "1.2倍"}],
            "dupont": {"net_margin": 0.52, "turnover": 0.45, "leverage": 1.8},
            "quality_score": 8,
            "red_flags": ["应收增速超营收"],
        },
        "valuation": {
            "net_cash_pe": 3.84,
            "peer_pe_median": 12.10,
            "stress": [
                {
                    "drop": 30.0, "net_profit": 202.02, "eps": 3.63,
                    "price": 27.76, "downside_pct": -30.3,
                },
                {
                    "drop": 50.0, "net_profit": 144.30, "eps": 2.60,
                    "price": 19.83, "downside_pct": -50.2,
                },
            ],
            "expectation_gap": "过度乐观",
        },
        "kelly": {
            "b": 2.0, "p": 0.42, "q": 0.58, "f_star": 0.13,
            "position": 0.065, "rating": "增持",
        },
        "red_team": [{"bull": "品牌强", "bear": "消费降级"}],
    }


def test_renders_all_sections():
    md = render_skeleton_md(_valid())
    assert "三层叙事" in md
    assert "法务会计" in md
    assert "估值" in md
    assert "Kelly" in md
    assert "增持" in md
    assert "估值安全边际" in md
    assert "3.84" in md  # 净现金调整 PE
    assert "12.10" in md  # 同业 PE 中位数


def test_dupont_partial_none_does_not_crash():
    # 回归：杜邦只给 net_margin、漏掉 turnover/leverage（"无值写 null"）时
    # 渲染器不得抛 TypeError，否则 0-LLM 降级会退化为"数据不可用"兜底。
    data = _valid()
    data["forensic_audit"]["dupont"] = {"net_margin": 0.52}
    md = render_skeleton_md(data)
    assert "杜邦拆解" in md
    assert "N/A" in md  # 缺失字段安全兜底


def test_renders_empty_skeleton():
    md = render_skeleton_md(None)
    assert "数据缺失" in md or "暂不可用" in md


def test_includes_probabilities():
    md = render_skeleton_md(_valid())
    assert "70%" in md or "0.7" in md
    assert "42%" in md or "0.42" in md


def test_renders_data_inference():
    # 降级路径应展开 data_inference（compile.md 要求，原遗漏）
    data = _valid()
    data["data_inference"] = [
        {"field": "fcf", "formula": "fcf≈经营现金流", "price_impact": "±5%"},
    ]
    md = render_skeleton_md(data)
    assert "缺失数据反推" in md
    assert "fcf" in md


def test_renders_self_critique():
    # 自我批判辩论：骨架有 self_critique 时必须渲染（原渲染器完全丢弃）
    data = _valid()
    data["self_critique"] = {
        "bear_attacks": [
            {"assumption": "毛利率维持", "attack": "渠道压价将侵蚀毛利"},
        ],
        "judge": "攻击部分成立，已在保守目标价体现",
    }
    md = render_skeleton_md(data)
    assert "自我批判" in md
    assert "渠道压价" in md
    assert "裁判回应" in md


def test_renders_stress_test():
    # 极端压力测试：骨架有 stress_test 时必须渲染（原渲染器完全丢弃）
    data = _valid()
    data["stress_test"] = {
        "scenario": "需求腰斩",
        "floor_price": 1200.0,
        "floor_downside_pct": -0.2,
        "verdict": "底线价具备股息支撑",
    }
    md = render_skeleton_md(data)
    assert "极端压力测试" in md
    assert "需求腰斩" in md
    assert "1200" in md
    assert "底线价" in md


def test_renders_valuation_safety_margin():
    # 估值安全边际(净现金PE / 同业PE中位数 / 压力测试),不输出目标价
    data = _valid()
    md = render_skeleton_md(data)
    assert "估值安全边际" in md
    assert "净现金调整 PE" in md
    assert "同业 PE 中位数" in md
    assert "压力" in md
    assert "27.76" in md  # 压力股价
    # 严禁三档目标价
    assert "保守" not in md or "目标价" not in md
