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
from typing import Any

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
        price = _first_float(text, r"(?:最新价|股价)\s*([\d.]+)")
        mcap = _first_float(text, r"市值\s*([\d.]+)\s*(?:亿|万元?)")
        rev_g = _first_float(text, r"营收(?:增速|增长)\s*([\d.]+)")
        np_g = _first_float(text, r"净利(?:增速|增长|润增速)\s*([\d.]+)")

        peers.append(
            {
                "name": name,
                "price": price if price is not None else 0.0,
                "pe": pe if pe is not None else 0.0,
                "pb": pb if pb is not None else 0.0,
                "roe": roe if roe is not None else 0.0,
                "np_cagr": np_cagr if np_cagr is not None else 0.0,
                "rev_g": rev_g if rev_g is not None else 0.0,
                "np_g": np_g if np_g is not None else 0.0,
                "mcap": mcap if mcap is not None else 0.0,
                "self": name == self_fin.symbol or name == getattr(self_fin, "name", ""),
            }
        )
    return peers


async def run(
    name: str,
    self_fin: FinancialData,
    search_fn: Callable[[str], Any] | None = None,
) -> dict[str, object]:
    """组合 entry point。

    真实场景下 orchestrator 注入 ``execute_web_search``(async),此处 await 后组合;
    无注入时上游依赖此工具 → 返 ``{"__partial__":"no_data"}`` 降级。
    ``search_fn`` 为 async 可调用对象(返回 HTML 字符串),调用方需 await。
    """
    try:
        if not name or not self_fin:
            return {"__partial__": "no_data", "peers": [], "industry": ""}

        industry = _detect_industry(name)
        peers: list[dict[str, object]] = []
        query = build_search_query(name, industry)
        # 主路径:web 搜索(由 orchestrator 注入 search_fn)
        if search_fn is not None:
            try:
                html = await search_fn(query)
                if html:
                    peers = parse(html, self_fin)
            except Exception as e:
                logger.debug("[peer_compare] web search failed: %s", e)
        # 兜底:web 无果 → akshare 行业板块(真实 PE/PB,更可靠)
        if not peers:
            peers = await _akshare_peers(self_fin, industry)
        if not peers:
            return {"__partial__": "no_data", "peers": [], "industry": industry}
        return {
            "peers": peers,
            "industry": industry,
            "_query": query if search_fn is not None else None,
        }
    except Exception as e:
        logger.debug("[peer_compare] partial: %s: %s", type(e).__name__, e)
        return {"__partial__": "no_data", "peers": [], "industry": ""}


async def _akshare_peers(self_fin: FinancialData, industry: str) -> list[dict[str, object]]:
    """akshare 行业板块兜底:返回同行(真实 PE/PB)。web 搜索无果时启用。

    全链路 lazy import + try/except:任一环节失败(列名差异/网络/WAF)均返回 [],
    不影响主流程(peer 为空 → 渲染层中位数行不出现,与旧行为一致)。
    """
    try:
        import akshare as ak

        symbol = getattr(self_fin, "symbol", "") or ""
        if not symbol:
            return []
        # 1) 个股所属行业(如 "白色家电")
        try:
            info = ak.stock_individual_info_em(symbol=symbol) or {}
            ind = (info.get("行业") or industry) if isinstance(info, dict) else industry
        except Exception:
            ind = industry
        if not ind:
            return []
        # 2) 行业 → 板块名称(如 "家电")
        boards = ak.stock_board_industry_name_em()
        board_name = _match_board(ind, boards)
        if not board_name:
            return []
        # 3) 板块成分股(含 市盈率-动态 / 市净率)
        cons = ak.stock_board_industry_cons_em(symbol=board_name)
        if cons is None or getattr(cons, "empty", lambda: True)():
            return []
        self_sym = symbol
        peers: list[dict[str, object]] = []

        def _flt(row: Any, col: str, default: float = 0.0) -> float:
            v = row.get(col)
            try:
                return float(v) if v is not None else default
            except (TypeError, ValueError):
                return default

        for _, r in cons.iterrows():
            code = str(r.get("代码", "") or "")
            if code == self_sym:
                continue
            peers.append(
                {
                    "name": str(r.get("名称", "") or ""),
                    "price": _flt(r, "最新价"),
                    "pe": _flt(r, "市盈率-动态"),
                    "pb": _flt(r, "市净率"),
                    "roe": 0.0,
                    "np_cagr": 0.0,
                    "rev_g": 0.0,
                    "np_g": 0.0,
                    "mcap": _flt(r, "总市值") or _flt(r, "流通市值"),
                    "self": False,
                }
            )
        return peers
    except Exception as e:
        logger.debug("[peer_compare] akshare fallback failed: %s: %s", type(e).__name__, e)
        return []


def _match_board(industry: str, boards: Any) -> str:
    """从板块列表(含 板块名称)中按子串匹配行业对应的板块名称。"""
    if not industry or boards is None:
        return ""
    try:
        for _, r in boards.iterrows():
            bn = str(r.get("板块名称", "") or "")
            if not bn:
                continue
            if industry in bn or bn in industry:
                return bn
    except Exception:
        return ""
    return ""


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
