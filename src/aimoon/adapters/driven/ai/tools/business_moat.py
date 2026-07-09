"""业务护城河分析工具(纯函数)。

输入 single + research + 社媒 + 历史 OCF,输出 SWOT / 护城河来源 / OCF 含金量 / 上下游议价。
任一必需输入缺失 → ``{"__partial__":"missing_<X>"}``。
"""
from __future__ import annotations

import logging

from aimoon.core.domain.entities.financial import FinancialData
from aimoon.core.domain.entities.research import ResearchReportData
from aimoon.core.domain.entities.social import SocialPost

logger = logging.getLogger(__name__)

_MOAT_KEYWORDS: dict[str, list[str]] = {
    "brand": ["品牌", "高端", "溢价", "老字号", "龙头", "知名度"],
    "channel": ["渠道", "分销", "直销", "经销商", "终端", "门店", "营销"],
    "cost": ["成本", "规模效应", "低毛利竞争", "成本优势", "自制率"],
    "network_effect": ["网络效应", "用户粘性", "平台", "生态", "双边"],
    "patent": ["专利", "配方", "技术壁垒", "独家", "工艺"],
}


def run(
    self_fin: FinancialData | None,
    research: ResearchReportData | None,
    social_posts: list[SocialPost] | tuple[SocialPost, ...] | None,
    history_ocf: list[FinancialData] | None,
) -> dict[str, object]:
    try:
        if self_fin is None:
            return {"__partial__": "missing_self_fin"}
        if research is None:
            return {"__partial__": "missing_research"}
        if social_posts is None:
            return {"__partial__": "missing_social_posts"}
        if history_ocf is None:
            return {"__partial__": "missing_history_ocf"}

        corr = _corpus(research, social_posts)
        moat_sources = _detect_moat(corr, self_fin)
        ocf_quality = _ocf_quality(self_fin, history_ocf)
        up, down = _bargaining(corr, self_fin)
        swot = _build_swot(corr, self_fin, moat_sources, ocf_quality)

        return {
            "swot": swot,
            "moat_sources": moat_sources,
            "ocf_quality": round(ocf_quality, 4),
            "upstream_bargaining": round(up, 4),
            "downstream_bargaining": round(down, 4),
        }
    except Exception as e:
        logger.debug("[business_moat] partial: %s: %s", type(e).__name__, e)
        return {"__partial__": "computation_error"}


def _corpus(
    research: ResearchReportData,
    social_posts: list[SocialPost] | tuple[SocialPost, ...] | None,
) -> str:
    parts: list[str] = []
    for r in research.reports or []:
        parts.append(r.title)
        parts.append(r.institution)
        parts.append(r.industry)
        parts.append(r.rating)
    for p in social_posts or []:
        parts.append(p.title)
        parts.append(p.content)
    return "\n".join(parts)


def _detect_moat(corr: str, self_fin: FinancialData) -> list[str]:
    hits: list[str] = []
    for source, keywords in _MOAT_KEYWORDS.items():
        if any(k in corr for k in keywords):
            hits.append(source)
    # ROE 持续 20%+ 视为品牌/定价权护城河
    if self_fin.roe >= 20.0 and "brand" not in hits:
        hits.append("brand")
    if not hits:
        hits.append("channel")
    return hits


def _ocf_quality(self_fin: FinancialData, history_ocf: list[FinancialData]) -> float:
    """OCF/净利润含金量:1 为满分,<1 表示利润变现差。"""
    if self_fin.net_profit == 0:
        return 0.0
    base = self_fin.operating_cf / self_fin.net_profit
    if len(history_ocf) >= 2:
        ratios = [f.operating_cf / f.net_profit for f in history_ocf if f.net_profit != 0]
        ratios.append(base)
        return sum(ratios) / len(ratios)
    return base


def _bargaining(corr: str, self_fin: FinancialData) -> tuple[float, float]:
    """上下游议价(0-1)。高 ROE / 预收 / 定价权关键词 → 下游议价高,上游偏低。"""
    downstream_terms = ["定价权", "提价", "预收", "高端", "品牌溢价", "供不应求"]
    upstream_terms = ["原材料", "成本上涨", "供应商集中", "大宗商品", "上游压力"]
    down = sum(1 for k in downstream_terms if k in corr)
    up = sum(1 for k in upstream_terms if k in corr)

    total = down + up
    if total == 0:
        # ROE 代理:高 ROE 大概率下游议价强
        base = min(max(self_fin.roe / 100.0, 0.0), 1.0)
        return 0.5, 0.3 + base * 0.4
    downstream = down / total
    upstream = up / total
    return upstream, downstream


def _build_swot(
    corr: str,
    self_fin: FinancialData,
    moat_sources: list[str],
    ocf_quality: float,
) -> dict[str, object]:
    cn_source = {_src_label(s) for s in moat_sources}
    strengths = sorted(cn_source)
    weaknesses: list[str] = []
    if ocf_quality < 0.7:
        weaknesses.append("经营现金流含金量偏低")
    if self_fin.revenue_yoy < 0:
        weaknesses.append("营收同比下滑")
    if not strengths:
        weaknesses.append("未识别显性护城河来源")

    opportunities = _match(corr, ["渠道下沉", "海外", "新产品", "提价空间", "消费升级"])
    risks = _match(corr, ["政策", "反垄断", "消费降级", "竞争加剧", "估值偏高", "库存"])
    if not risks:
        risks.append("未在研报/舆情中识别显性风险主题")

    return {
        "strengths": strengths if strengths else ["龙头地位稳固(默认)"],
        "weaknesses": weaknesses,
        "opportunities": opportunities,
        "risks": risks,
    }


def _src_label(s: str) -> str:
    return {
        "brand": "品牌护城河",
        "channel": "渠道优势",
        "cost": "成本领先",
        "network_effect": "网络效应",
        "patent": "专利/技术壁垒",
    }.get(s, s)


def _match(corr: str, keywords: list[str]) -> list[str]:
    return [k for k in keywords if k in corr]
