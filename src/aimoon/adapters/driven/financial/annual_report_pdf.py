"""年报 PDF 附注解析器 — 消 8.1 缺失清单(C 组附注级数据)。

目标数据(标准三表无,需年报 PDF 附注):
- 应收账款保理/证券化/终止确认
- 应收账款账龄
- 存货跌价准备
- 关联交易
- 应付账款账龄 / 结构

实现: 巨潮资讯(cninfo)公告查询 API 取最新年报 PDF → httpx 下载 →
pdfplumber 内存解析(BytesIO,不落盘,规避沙箱 safe-delete 钩子)→
对 5 个主题抽取**原文摘录**(verbatim excerpt),供 LLM 在正文引用
「见年报附注表」,而非重复写「数据缺失」。

设计取舍: 年报 PDF 附注为自由文本表格,跨公司/跨年排版差异大,
数值结构化抽取可靠性低;故 v1 以**原文摘录**为核心交付(模型可读真实措辞),
不强行解析为可能错误的数值,避免幻觉。单源失败返回 {} 永不 abort 主流程。
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_CNINFO_QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept": "application/json",
    "Referer": "https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
    "Origin": "https://www.cninfo.com.cn",
}
# 五个目标主题: (topic 标签, 在该主题摘录里优先命中的关键词)。
# 关键词按"最具体→较泛"排序: 先匹配精确披露措辞(如「终止确认」「账龄分析」),
# 避免误命中无关的「商业保理」子公司名或资产负债表单行。
_TOPICS: list[tuple[str, list[str]]] = [
    (
        "应收账款保理与终止确认",
        ["终止确认", "无追索权", "应收账款保理", "已转让", "已转移", "证券化", "保理"],
    ),
    (
        "应收账款账龄",
        ["应收账款账龄", "应收账款 账龄", "账龄分析", "按账龄", "应收账款"],
    ),
    ("存货跌价准备", ["存货跌价准备", "存货跌价", "库存商品跌价"]),
    ("关联交易", ["关联交易"]),
    ("应付账款账龄与结构", ["应付账款账龄", "应付账款 账龄", "账龄分析", "应付账款"]),
]
# 排除这些标题(摘要/英文版/季报),只取正文年报
_SKIP_TITLE_RE = re.compile(r"(摘要|英文版|三季度|半年度|一季度|半年|季度报告|业绩)")
_SOURCE = "巨潮资讯 PDF(年报)"


def _empty_footnotes(report_title: str = "") -> dict[str, Any]:
    """统一的空结果(单源失败 / 未命中)。"""
    return {"report_title": report_title, "source": _SOURCE, "available": False, "excerpts": []}


def _cninfo_query(symbol: str, stock_name: str) -> list[dict]:
    """巨潮公告查询: 取该公司最新年报 PDF 元信息(同步,线程内执行)。

    用 searchkey="{公司名} 年度报告" 全文本检索(实测对 000651 返回 30 条
    全部属于该公司),再按 secCode 过滤、按公告时间倒序,取第一条正文年报。
    """
    searchkey = f"{stock_name} 年度报告" if stock_name else f"{symbol} 年度报告"
    payload = {
        "stock": "",
        "pageNum": "1",
        "pageSize": "30",
        "tabKey": "fulltext",
        "category": "",
        "seDate": "",
        "searchkey": searchkey,
        "isHLtitle": "true",
        "sortName": "announcementTime",
        "sortType": "desc",
    }
    # 同步 Client: 本函数在 asyncio.to_thread 内执行,无法 await AsyncClient。
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(_CNINFO_QUERY, data=payload, headers=_HEADERS)
    if resp.status_code != 200:
        return []
    data = resp.json()
    items = data.get("announcements") or []
    owned = [i for i in items if symbol in (i.get("secCode") or "")]
    out = []
    for it in owned:
        title = re.sub(r"<[^>]+>", "", str(it.get("announcementTitle", ""))).strip()
        if not title or _SKIP_TITLE_RE.search(title):
            continue
        adjunct = it.get("adjunctUrl") or ""
        if not adjunct:
            continue
        out.append(
            {
                "title": title,
                "pdf_url": f"https://static.cninfo.com.cn/{adjunct}",
                "time": it.get("announcementTime", ""),
            }
        )
    return out


async def _download_pdf(pdf_url: str) -> bytes | None:
    async with httpx.AsyncClient(
        timeout=60.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
    ) as client:
        r = await client.get(pdf_url)
        if r.status_code != 200 or not r.content:
            return None
        return r.content


def _extract_text(pdf_bytes: bytes) -> str:
    """pdfplumber 内存解析(BytesIO),不落盘规避沙箱 unlink 钩子。"""
    import pdfplumber  # 懒导入: 仅本函数需要,且 pdfplumber 较重

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        parts: list[str] = []
        for page in pdf.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 — 单页解析失败不拖垮整体
                continue
    return "\n".join(parts)


def _clean(text: str) -> str:
    """折叠空白,便于摘录阅读。"""
    return re.sub(r"\s+", " ", text).strip()


def _excerpt_for(
    text: str,
    keywords: list[str],
    window_before: int = 120,
    window_after: int = 360,
) -> str | None:
    """返回第一个命中关键词的上下文窗口(清洗后);未命中返回 None。"""
    for kw in keywords:
        idx = text.find(kw)
        if idx >= 0:
            start = max(0, idx - window_before)
            end = min(len(text), idx + window_after)
            return _clean(text[start:end])
    return None


# ===== 分季度主要财务指标 / 分地区(内销/外销) 结构化解析(消 8.1 缺失清单 #1/#2) =====
# 这两块数据来自年报 PDF 的固定披露章节(「八、分季度主要财务指标」「营业收入构成-分地区」),
# 与 5 主题附注走同一份巨潮 PDF,零新增网络源,且天然绕开东财 WAF 软封。
_QUARTERLY_QORDER = ["第一季度", "第二季度", "第三季度", "第四季度"]
# 4 个连续裸数字(无百分号间隔): 用于「分季度主要财务指标」行的营收/净利单季值
_NUM4_RE = (
    r"([\d,]+\.?\d*)\s+([\d,]+\.?\d*)\s+([\d,]+\.?\d*)\s+([\d,]+\.?\d*)"
)
# 分地区-营收行: 内销/外销 营收 占比% 上年营收 上年占比% 同比%
_REGION_REV_RE = re.compile(
    r"^(内销|境内|外销|境外)[\u4e00-\u9fa5\-]*\s+"
    r"([\d,]+\.?\d*)\s+([\d.]+)%\s+([\d,]+\.?\d*)\s+([\d.]+)%\s+([-\d.]+)%"
)
# 分地区-毛利率行: 内销/外销 营收 营业成本 毛利率%
_REGION_MARGIN_RE = re.compile(
    r"^(内销|境内|外销|境外)[\u4e00-\u9fa5\-]*\s+"
    r"([\d,]+\.?\d*)\s+([\d,]+\.?\d*)\s+([\d.]+)%"
)


def parse_quarterly_breakdown_from_text(text: str, report_title: str = "") -> dict[str, Any]:
    """纯函数: 从年报「分季度主要财务指标」章节抽取单季营收/归母净利润。

    返回 {available, source, quarters:[{quarter, revenue_yi, net_profit_yi,
    revenue_yoy?, net_profit_yoy?}]}。单季值直接给出(非累计),与「判断营收下滑是否见底」强相关。
    同比需上年同期年报,由 fetch_annual_report_footnotes 合并(_compute_quarterly_yoy)。
    """
    if not text:
        return {"available": False, "quarters": [], "source": _SOURCE}
    lines = text.splitlines()
    header_idx = -1
    for i, line in enumerate(lines):
        if "分季度主要财务指标" in line and "第一季度" in line and "第四季度" in line:
            header_idx = i
            break
    if header_idx < 0:
        for i, line in enumerate(lines):
            if all(
                q in line
                for q in ("第一季度", "第二季度", "第三季度", "第四季度")
            ):
                header_idx = i
                break
    if header_idx < 0:
        return {"available": False, "quarters": [], "source": _SOURCE}

    revenue: list[float] | None = None
    net_profit: list[float] | None = None
    for line in lines[header_idx : header_idx + 14]:
        s = line.strip()
        m = re.match(r"^营业收入\s+" + _NUM4_RE, s)
        if m and revenue is None:
            revenue = [float(x.replace(",", "")) for x in m.groups()]
            continue
        m = re.match(r"^归属于上市公司股东的净利润\s+" + _NUM4_RE, s)
        if m and net_profit is None:
            net_profit = [float(x.replace(",", "")) for x in m.groups()]
            continue
    if not revenue and not net_profit:
        return {"available": False, "quarters": [], "source": _SOURCE}
    quarters = []
    for i, q in enumerate(_QUARTERLY_QORDER):
        quarters.append(
            {
                "quarter": q,
                "revenue_yi": round(revenue[i] / 1e8, 2) if revenue else None,
                "net_profit_yi": round(net_profit[i] / 1e8, 2) if net_profit else None,
            }
        )
    return {"available": True, "quarters": quarters, "source": _SOURCE}


def _compute_quarterly_yoy(cur: dict, prev: dict) -> dict:
    """用上年同期单季值补全同比(单季 vs 单季)。原地返回 cur。"""
    if not isinstance(prev, dict) or not prev.get("available") or not prev.get("quarters"):
        return cur
    prev_map = {q["quarter"]: q for q in prev["quarters"]}
    for q in cur.get("quarters", []):
        pq = prev_map.get(q["quarter"])
        if not pq:
            continue
        if q.get("revenue_yi") is not None and pq.get("revenue_yi"):
            q["revenue_yoy"] = round(
                (q["revenue_yi"] - pq["revenue_yi"]) / pq["revenue_yi"] * 100, 2
            )
        if q.get("net_profit_yi") is not None and pq.get("net_profit_yi"):
            q["net_profit_yoy"] = round(
                (q["net_profit_yi"] - pq["net_profit_yi"]) / pq["net_profit_yi"] * 100, 2
            )
    return cur


def parse_region_breakdown_from_text(text: str) -> dict[str, Any]:
    """纯函数: 从年报「营业收入构成-分地区」抽取内销/外销营收、占比、同比、毛利率。

    返回 {available, source, regions:[{name, revenue_yi, ratio, yoy, gross_margin}]}。
    营收/占比/同比来自第一张分地区表,毛利率来自第二张(毛利率)分地区表,按键(内销/外销)合并。
    注意: 这是公司主营业务层面拆分,非空调业务专属(空调专属无公开确定性来源)。
    """
    if not text:
        return {"available": False, "regions": [], "source": _SOURCE}
    regions: dict[str, dict] = {}
    for line in text.splitlines():
        s = line.strip()
        m = _REGION_REV_RE.match(s)
        if m:
            name = m.group(1)
            r = regions.setdefault(name, {})
            r["revenue_yi"] = round(float(m.group(2).replace(",", "")) / 1e8, 2)
            r["ratio"] = float(m.group(3))
            r["yoy"] = float(m.group(6))
            continue
        m = _REGION_MARGIN_RE.match(s)
        if m:
            name = m.group(1)
            r = regions.setdefault(name, {})
            r["gross_margin"] = float(m.group(4))
    if not regions:
        return {"available": False, "regions": [], "source": _SOURCE}
    return {
        "available": True,
        "regions": [{"name": k, **v} for k, v in regions.items()],
        "source": _SOURCE,
    }


def parse_footnotes_from_text(text: str, report_title: str = "") -> dict[str, Any]:
    """纯函数: 从年报全文抽取 5 个主题的原文摘录。

    返回 {'report_title','source','available','excerpts':[{topic,text}]}。
    任一批注未命中关键词则该主题不出现在 excerpts;excerpts 为空时 available=False。
    """
    if not text:
        return _empty_footnotes(report_title)
    excerpts: list[dict[str, str]] = []
    for topic, keywords in _TOPICS:
        ex = _excerpt_for(text, keywords)
        if ex:
            excerpts.append({"topic": topic, "text": ex[:280]})
    return {
        "report_title": report_title,
        "source": "巨潮资讯 PDF(年报)",
        "available": bool(excerpts),
        "excerpts": excerpts,
    }


async def fetch_annual_report_footnotes(symbol: str, stock_name: str = "") -> dict[str, Any]:
    """端到端: cninfo 取年报 PDF → 下载 → 解析附注摘录。

    返回 parse_footnotes_from_text 的结构;任何单源失败返回
    {'available': False, 'excerpts': []},永不 abort 主流程。
    """
    try:
        metas = await asyncio.to_thread(_cninfo_query, symbol, stock_name)
        if not metas:
            logger.debug("[annual_report_pdf] 未找到 %s 年报公告", symbol)
            return _empty_footnotes()
        meta = metas[0]
        pdf = await _download_pdf(meta["pdf_url"])
        if not pdf:
            return _empty_footnotes(meta["title"])
        text = await asyncio.to_thread(_extract_text, pdf)
        result = parse_footnotes_from_text(text, report_title=meta["title"])
        # 分季度主要财务指标(单季营收/归母净利,见年报「八、分季度主要财务指标」)—
        # 确定性采集,消 8.1 缺失清单 #1。
        qb = parse_quarterly_breakdown_from_text(text, report_title=meta["title"])
        if qb["available"]:
            result["quarterly_breakdown"] = qb
        # 主营业务分地区(内销/外销,见年报「营业收入构成-分地区」)— 消 8.1 缺失清单 #2。
        rb = parse_region_breakdown_from_text(text)
        if rb["available"]:
            result["region_breakdown"] = rb
        # 上年同期年报(用于单季同比, best-effort): 取 metas[1](按公告时间倒序的次新)。
        if len(metas) > 1:
            try:
                pdf2 = await _download_pdf(metas[1]["pdf_url"])
                if pdf2:
                    text2 = await asyncio.to_thread(_extract_text, pdf2)
                    qb_prev = parse_quarterly_breakdown_from_text(text2)
                    if qb_prev["available"]:
                        result["quarterly_breakdown"] = _compute_quarterly_yoy(qb, qb_prev)
            except Exception:  # noqa: BLE001 — 上年同期缺失仅影响同比,不影响主表
                pass
        logger.warning(
            "[annual_report_pdf] %s 年报解析: 附注 %d/%d 主题 | 分季度 %s | 分地区 %s "
            "(来源: 巨潮 %s)",
            symbol, len(result["excerpts"]), len(_TOPICS),
            "✓" if result.get("quarterly_breakdown") else "✗",
            "✓" if result.get("region_breakdown") else "✗",
            meta["title"],
        )
        return result
    except Exception as e:  # noqa: BLE001 — 单源失败永不 abort 主流程
        logger.warning("[annual_report_pdf] %s 解析失败: %s", symbol, e)
        return _empty_footnotes()
