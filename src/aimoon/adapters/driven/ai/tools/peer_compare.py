"""同行业竞品对比工具(正则解析 + 组合 search)。

本模块 **不直接发起网络搜索** —— `build_search_query` 生成搜索串、
`parse` 用正则从 Bing 风格列表 HTML 抽竞品,
`run(name, self_fin, search_fn=...)` 按注入的 search 函数组合二者。
本注入点使单元测试可用假 HTML,也供 orchestrator 注入 ``execute_web_search``。
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable

from aimoon.core.domain.entities.financial import FinancialData

logger = logging.getLogger(__name__)

# 简易行业关键词映射(公司名/关键词 → 行业中文名)。
_INDUSTRY_MAP: list[tuple[list[str], str]] = [
    (["美的", "格力", "海尔", "海信", "家电", "空调", "冰箱", "洗衣机"], "白色家电"),
    (["茅台", "五粮液", "泸州", "汾酒", "洋河", "白酒", "啤酒"], "白酒"),
    (["宁德", "比亚迪", "锂", "动力电池", "新能源车"], "新能源"),
    (["恒瑞", "药", "生物制药", "医药"], "医药生物"),
    (["招商", "工商", "农行", "中行", "建行", "银行", "证券", "保险"], "金融"),
]

# 已知公司 → 行业(常用标杆)。
_COMPANY_INDUSTRY: dict[str, str] = {
    "贵州茅台": "白酒",
    "五粮液": "白酒",
    "美的集团": "白色家电",
    "格力电器": "白色家电",
    "海尔智家": "白色家电",
    "宁德时代": "新能源",
    "比亚迪": "新能源",
    "招商银行": "金融",
}


def build_search_query(name: str, industry: str = "") -> str:
    """构造同行竞品搜索串,供 web_search_tool.execute_web_search 使用。"""
    seed = industry or _COMPANY_INDUSTRY.get(name, "")
    if seed:
        return f"{name} 同行竞品 {seed} 市值 PE PB ROE 近三年净利润CAGR 对比"
    return f"{name} 同行对比 竞争对手 同行业 PE ROE 市值 近3年CAGR"


def parse(html_or_text: str, self_fin: FinancialData) -> list[dict[str, object]]:
    """从 Bing 风格 <li class="b_algo"> 列表解析竞品 (name, pe, pb, roe, np_cagr)。"""
    if not html_or_text:
        return []
    peers: list[dict[str, object]] = []
    blocks = re.findall(r'<li class="b_algo".*?</li>', html_or_text, re.DOTALL)
    for block in blocks:
        anchors = re.findall(r"<a[^>]*>(.*?)</a>", block, re.DOTALL)
        if not anchors:
            continue
        anchor = anchors[0]
        text = re.sub(r"<[^>]+>", "", anchor).strip()
        if not text:
            continue

        name = _extract_name(text)
        if not name:
            continue
        pe = _first_float(text, r"PE\s*(\d+(?:\.\d+)?)")
        pb = _first_float(text, r"PB\s*(\d+(?:\.\d+)?)")
        roe = _first_float(text, r"ROE\s*(\d+(?:\.\d+)?)")
        np_cagr = _first_float(text, r"(?:近三年)?净利润CAGR\s*(\d+(?:\.\d+)?)")

        peers.append(
            {
                "name": name,
                "pe": pe if pe is not None else 0.0,
                "pb": pb if pb is not None else 0.0,
                "roe": roe if roe is not None else 0.0,
                "np_cagr": np_cagr if np_cagr is not None else 0.0,
                "self": name == self_fin.symbol or name == getattr(self_fin, "name", ""),
            }
        )
    return peers


def run(
    name: str,
    self_fin: FinancialData,
    search_fn: Callable[[str], str | None] | None = None,
) -> dict[str, object]:
    """组合 entry point。

    真实场景下 orchestrator 注入 ``execute_web_search``,此处仅组合调用;
    无注入时上游依赖此工具 → 返 ``{"__partial__":"no_data"}`` 降级。
    """
    try:
        if not name or not self_fin:
            return {"__partial__": "no_data", "peers": [], "industry": ""}

        if search_fn is None:
            logger.info("[peer_compare] 未提供 search_fn,返回 partial(由 orchestrator 注入)")
            return {"__partial__": "no_data", "peers": [], "industry": ""}

        industry = _detect_industry(name)
        query = build_search_query(name, industry)
        html = search_fn(query)
        if not html:
            return {"__partial__": "no_data", "peers": [], "industry": industry}

        peers = parse(html, self_fin)
        return {
            "peers": peers,
            "industry": industry,
            "_query": query,
        }
    except Exception as e:
        logger.debug("[peer_compare] partial: %s: %s", type(e).__name__, e)
        return {"__partial__": "no_data", "peers": [], "industry": ""}


def _extract_name(text: str) -> str:
    """从锚文本前半段抽公司名(去掉末尾数字/字母代码)。"""
    head = re.split(r"\s+", text, maxsplit=1)[0] if text else ""
    head = re.sub(r"^[，,、\s]+|[，,、\s]+$", "", head)
    head = re.sub(r"\d{5,8}$", "", head).strip()
    return head


def _first_float(text: str, pattern: str) -> float | None:
    m = re.search(pattern, text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def _detect_industry(name: str) -> str:
    known = _COMPANY_INDUSTRY.get(name, "")
    if known:
        return known
    for keywords, ind in _INDUSTRY_MAP:
        if any(k in name for k in keywords):
            return ind
    return ""
