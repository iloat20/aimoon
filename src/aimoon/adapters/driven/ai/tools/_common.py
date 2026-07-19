"""Shared pure helpers for :mod:`aimoon.adapters.driven.ai.tools`.

Extracted to eliminate cross-module DRY duplication (audit P2.2):
- ``_first_year_ocf``           (was duplicated in valuation.py + fcf_dividend.py)
- ``_first_year_investing``     (was duplicated in valuation.py + fcf_dividend.py)
- ``_capex``                   (was duplicated in valuation.py + fcf_dividend.py)
"""
from __future__ import annotations


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


def _capex(ocf: float, investing_cf: float, real_capex: float = 0.0) -> float:
    """capex 代理: 优先用真实 capex(购建固定资产现金)。

    关键护栏: 真实 capex 缺失时,**绝不**直接拿投资现金流净额绝对值 ``|investing_cf|``
    当 capex —— 对格力这类大量购买理财(结构性存款)的公司,投资净额(如 -486 亿)
    主要是理财净流出而非 PP&E 支出,误用会令 FCF 被虚构为巨额负值、触发 DDM 退化
    与"分红不可持续"的假结论。仅当 ``|investing_cf|`` 明显小于 OCF(多为真实资本开支)时
    才作代理;否则返回 0(→ FCF=OCF,安全高估,绝不大额虚构为负)。
    """
    if real_capex and real_capex > 0:
        return real_capex
    # 仅在投资净流出不超 OCF 时才当 capex 代理(典型重资产公司 capex ≪ OCF)
    if investing_cf < 0 and abs(investing_cf) <= max(ocf, 0.0):
        return -investing_cf
    # 实在没有真实 capex 且无可靠代理: 返回 0(调用方应以 OCF 作为 FCF 下限)
    return 0.0
