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
            "targets": {"conservative": 1500, "neutral": 1800, "optimistic": 2100},
            "implied_g": 0.04,
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
    assert "1500" in md
    assert "1800" in md


def test_renders_empty_skeleton():
    md = render_skeleton_md(None)
    assert "数据缺失" in md or "暂不可用" in md


def test_includes_probabilities():
    md = render_skeleton_md(_valid())
    assert "70%" in md or "0.7" in md
    assert "42%" in md or "0.42" in md
