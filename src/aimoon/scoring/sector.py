"""板块动量信号 — 含弱势板块惩罚"""

from __future__ import annotations

from aimoon.indicators.technical import TechInd
from aimoon.models import Signal


def score_sector(ti: TechInd, *, code: str = "", ctx: dict | None = None) -> Signal | None:
    if not ctx:
        return None
    top_pct = ctx.get("top_pct", 5)
    sector_map = ctx.get("sector_map", {})
    top_sectors = ctx.get("top_sectors", set())
    bottom_sectors = ctx.get("bottom_sectors", set())
    sector = sector_map.get(code)
    if sector and sector in top_sectors:
        return Signal("sector_top", f"强势板块(Top{top_pct}%)", +3, category="alpha")
    if sector and sector in bottom_sectors:
        return Signal("sector_weak", "弱势板块(Bottom30%)", -2, category="alpha")
    return None
