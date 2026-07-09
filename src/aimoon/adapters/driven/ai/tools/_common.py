"""Shared pure helpers for :mod:`aimoon.adapters.driven.ai.tools`.

Extracted to eliminate cross-module DRY duplication (audit P2.2):
- ``INDUSTRIAL_CAPEX_OCF_RATIO``  (was duplicated in valuation.py + fcf_dividend.py)
- ``_first_year_ocf``           (was duplicated in valuation.py + fcf_dividend.py)
- ``_first_year_investing``     (was duplicated in valuation.py + fcf_dividend.py)
- ``_capex``                   (was duplicated in valuation.py + fcf_dividend.py)
- ``_hist_pe_anchor``          (was duplicated in risk_quant.py + scenario_prob.py)
"""
from __future__ import annotations

# 工业类 capex 兜底: OCF 的 30% (investing_cf 缺失时)
INDUSTRIAL_CAPEX_OCF_RATIO = 0.30


def _first_year_ocf(fin: dict) -> float:
    """首年经营性现金流 (operating_cf)。"""
    years = fin.get("years") or []
    if years and isinstance(years[0], dict):
        v = years[0].get("operating_cf")
        return float(v) if v is not None else 0.0
    return 0.0


def _first_year_investing(fin: dict) -> float:
    """首年投资性现金流 (investing_cf)。"""
    years = fin.get("years") or []
    if years and isinstance(years[0], dict):
        v = years[0].get("investing_cf")
        return float(v) if v is not None else 0.0
    return 0.0


def _capex(ocf: float, investing_cf: float) -> float:
    """capex 代理: 投资现金流净流出绝对值; 否则工业兜底 OCF * 30%。"""
    if investing_cf < 0:
        return -investing_cf
    if ocf > 0:
        return ocf * INDUSTRIAL_CAPEX_OCF_RATIO
    return 0.0


def _hist_pe_anchor(fin_temporal: dict | None) -> float:
    """近 N 年 PE 均值 (估值历史锚); 缺失返 0.0。"""
    if not fin_temporal:
        return 0.0
    years = fin_temporal.get("years") or []
    vals: list[float] = []
    for y in years:
        pe = float((y.get("pe") if isinstance(y, dict) else 0) or 0.0)
        if pe > 0:
            vals.append(pe)
    return sum(vals) / len(vals) if vals else 0.0
