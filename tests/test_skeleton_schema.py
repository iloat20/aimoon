"""Tests for the analysis skeleton JSON schema."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from aimoon.adapters.driven.ai.pipeline.skeleton_schema import AnalysisSkeleton


def _valid_skeleton() -> dict:
    """Return a minimal valid skeleton dict for tests."""
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
            "red_flags": ["应收增速超营收"],
        },
        "valuation": {
            "targets": {"conservative": 1500, "neutral": 1800, "optimistic": 2100},
            "implied_g": 0.04,
            "expectation_gap": "过度乐观",
        },
        "kelly": {"b": 2.0, "p": 0.42, "q": 0.58, "f_star": 0.13, "position": 0.065, "rating": "增持"},
    }


def test_valid_skeleton_parses():
    sk = AnalysisSkeleton.model_validate(_valid_skeleton())
    assert sk.kelly.b == 2.0
    assert sk.forensic_audit.quality_score == 8


def test_missing_kelly_fails():
    data = _valid_skeleton()
    del data["kelly"]
    with pytest.raises(ValidationError):
        AnalysisSkeleton.model_validate(data)


def test_probability_range_clamped():
    data = _valid_skeleton()
    data["narratives"]["macro"]["probability"] = 1.5
    with pytest.raises(ValidationError):
        AnalysisSkeleton.model_validate(data)


def test_quality_score_range():
    data = _valid_skeleton()
    data["forensic_audit"]["quality_score"] = 15
    with pytest.raises(ValidationError):
        AnalysisSkeleton.model_validate(data)
