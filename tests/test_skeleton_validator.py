"""Tests for the programmatic skeleton validator (0 LLM)."""
from __future__ import annotations

from aimoon.adapters.driven.ai.pipeline.skeleton_validator import validate_skeleton


def _valid():
    return {
        "narratives": {
            "macro": {"probability": 0.7, "consensus": "x", "our_view": "y", "falsify": "z"},
            "industry": {"probability": 0.8, "consensus": "x", "our_view": "y", "falsify": "z"},
            "alpha": {"probability": 0.75, "consensus": "x", "our_view": "y", "falsify": "z"},
        },
        "composite_prob": 0.42,
        "forensic_audit": {
            "items": [{"item": "OCF", "status": "正常", "detail": "ok"}],
            "dupont": {"net_margin": 0.52, "turnover": 0.45, "leverage": 1.8},
            "quality_score": 8,
            "red_flags": [],
        },
        "valuation": {
            "targets": {"conservative": 1500, "neutral": 1800, "optimistic": 2100},
            "implied_g": 0.04,
            "expectation_gap": "过度乐观",
        },
        "kelly": {"b": 2.0, "p": 0.42, "q": 0.58, "f_star": 0.13, "position": 0.065, "rating": "增持"},
    }


def test_valid_skeleton_passes():
    result = validate_skeleton(_valid(), tables_md="")
    assert result["passed"] is True
    assert result["fixes_needed"] == []


def test_invalid_json_returns_fix():
    result = validate_skeleton("not json at all", tables_md="")
    assert result["passed"] is False
    assert any("JSON" in f for f in result["fixes_needed"])


def test_composite_prob_mismatch():
    data = _valid()
    data["composite_prob"] = 0.99
    result = validate_skeleton(data, tables_md="")
    assert result["passed"] is False
    assert any("复合概率" in f for f in result["fixes_needed"])


def test_kelly_formula_check():
    data = _valid()
    data["kelly"]["f_star"] = 0.99
    result = validate_skeleton(data, tables_md="")
    assert result["passed"] is False
    assert any("Kelly" in f for f in result["fixes_needed"])


def test_missing_valuation_targets():
    data = _valid()
    data["valuation"]["targets"] = {"conservative": 1500}
    result = validate_skeleton(data, tables_md="")
    assert result["passed"] is False
    assert any("目标价" in f for f in result["fixes_needed"])


def test_half_kelly_position_mismatch():
    # position 应为 f_star × 0.5（半凯利），不一致应被判出问题
    data = _valid()
    data["kelly"]["position"] = 0.0  # 漏算半凯利
    result = validate_skeleton(data, tables_md="")
    assert result["passed"] is False
    assert any("半凯利" in f for f in result["fixes_needed"])


def test_kelly_p_must_match_composite_prob():
    # Kelly 概率 p 应与复合看多概率一致
    data = _valid()
    data["kelly"]["p"] = 0.90
    result = validate_skeleton(data, tables_md="")
    assert result["passed"] is False
    assert any("p 与复合" in f for f in result["fixes_needed"])
