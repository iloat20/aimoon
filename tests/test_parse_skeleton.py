"""Tests for parse_skeleton_json - extract JSON from LLM output."""
from __future__ import annotations

from aimoon.adapters.driven.ai.pipeline.utils import parse_skeleton_json


def test_pure_json():
    raw = '{"kelly": {"b": 2.5}}'
    result = parse_skeleton_json(raw)
    assert result is not None
    assert result["kelly"]["b"] == 2.5


def test_json_in_code_fence():
    raw = 'Here is the skeleton:\n```json\n{"kelly": {"b": 2.5}}\n```\nDone.'
    result = parse_skeleton_json(raw)
    assert result is not None
    assert result["kelly"]["b"] == 2.5


def test_json_with_noise():
    raw = 'Analysis complete.\n{"kelly": {"b": 2.5}, "composite_prob": 0.27}\nEnd.'
    result = parse_skeleton_json(raw)
    assert result is not None
    assert result["composite_prob"] == 0.27


def test_invalid_returns_none():
    result = parse_skeleton_json("no json here at all")
    assert result is None


def test_empty_returns_none():
    result = parse_skeleton_json("")
    assert result is None
