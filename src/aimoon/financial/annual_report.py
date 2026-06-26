"""Financial report fetcher, PDF extractor, cache, and markdown exporter.

Fetches latest annual, semi-annual, and quarterly reports from cninfo.com.cn,
downloads PDFs, extracts key financial text, caches locally (30 days),
and can save the extracted data as a markdown file for AI review.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from ..config.settings import get_settings

_CNINFO_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept": "application/json",
    "Referer": "https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
    "Origin": "https://www.cninfo.com.cn",
}

_REPORT_TYPES = [
    ("年度报告", "年报"),
    ("半年度报告", "半年报"),
    ("季度报告", "季报"),
]

_MAX_EXTRACT_CHARS = 30000


def _cache_path(symbol: str) -> Path:
    settings = get_settings()
    cache_dir = settings.cache_path / "financial_reports"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{symbol}.json"


def _load_cache(symbol: str) -> dict | None:
    path = _cache_path(symbol)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
        cache_days = get_settings().financial_report_cache_days
        if (datetime.now() - cached_at).days > cache_days:
            return None
        return data
    except Exception as e:
        logging.warning("[annual_report_load_cache] %s: %s", type(e).__name__, e)
        return None


def _save_cache(symbol: str, data: dict) -> None:
    path = _cache_path(symbol)
    data["cached_at"] = datetime.now().isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes using pdfminer.six."""
    try:
        from pdfminer.high_level import extract_text
        text = extract_text(io.BytesIO(pdf_bytes))
        return text.strip()
    except Exception as e:
        logging.warning("[pdf_extract] %s: %s", type(e).__name__, e)
        return ""


def extract_financial_data_from_pdf(pdf_bytes: bytes) -> str:
    """Extract financial statements from PDF using pdfplumber.

    Extracts:
    - 合并利润表 (full table)
    - 合并资产负债表 (full table)
    - 合并现金流量表 (full table)
    - 营业收入和营业成本明细 (segment data from notes)

    Returns markdown-formatted string with full tables.
    """
    try:
        import pdfplumber
    except ImportError:
        text = _extract_text_from_pdf(pdf_bytes)
        return _extract_financial_section_fallback(text)

    result_parts = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages_text = []
        for page in pdf.pages:
            pages_text.append(page.extract_text() or "")

        # Extract report year from PDF text for dynamic column headers
        _yr = ""
        for _text in pages_text[:5]:
            _ym = re.search(r"(20\d{2})\s*年.*(?:年度|半年度|季度).*报告", _text)
            if _ym:
                _yr = _ym.group(1)
                break
        if not _yr:
            for _text in pages_text[:5]:
                _ym = re.search(r"(20\d{2})", _text)
                if _ym:
                    _yr = _ym.group(1)
                    break
        _y_cur = f"{_yr}年" if _yr else "本期"
        _y_prev = f"{int(_yr) - 1}年" if _yr else "上期"
        _y_end = f"{_y_cur}末" if _yr else "期末"
        _y_begin = f"{_y_cur}初" if _yr else "期初"

        # --- 1. Extract 三大报表 as structured tables ---
        def _find_table_pages(
            keywords: list[str], exclude: list[str] | None = None
        ) -> list[int]:
            """Find page indices matching all keywords."""
            exclude = exclude or []
            found = []
            for i, text in enumerate(pages_text):
                if all(kw in text for kw in keywords):
                    if not any(ex in text for ex in exclude):
                        found.append(i)
            return found

        def _parse_table_rows(text: str) -> list[list[str]]:
            """Parse financial table from page text."""
            rows = []
            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                _skip = [
                    "编制单位", "单位：", "项 目", "项　目", "附注",
                    "珠海格力", "年度报告", "合并",
                ]
                if _yr:
                    _skip.extend([_y_cur, _y_prev])
                if any(s in line for s in _skip):
                    continue
                # Match: item [ref] number number
                m = re.match(
                    r'^(.+?)\s+(?:五[、.]\d+(?:（\d+）)?|十六[、.]\d+(?:[（(]\d+[)）])?)\s+'
                    r'([\d,.\-()]+)\s+([\d,.\-()]+)\s*$',
                    line,
                )
                if m:
                    rows.append([
                        m.group(1).strip(),
                        m.group(2).strip(),
                        m.group(3).strip(),
                    ])
                    continue
                # Match: item number number (no ref)
                m = re.match(
                    r'^(.{2,}?)\s+([\d,.\-()]{5,})\s+([\d,.\-()]{5,})\s*$',
                    line,
                )
                if m:
                    item = m.group(1).strip()
                    if re.search(r'[\u4e00-\u9fff]', item) and len(item) < 30:
                        rows.append([item, m.group(2).strip(), m.group(3).strip()])
                        continue
                # Match: item [ref] single number
                m = re.match(
                    r'^(.+?)\s+(?:五[、.]\d+(?:（\d+）)?)\s+([\d,.\-()]{5,})\s*$',
                    line,
                )
                if m:
                    rows.append([m.group(1).strip(), m.group(2).strip(), ""])
                    continue
                # Match: item single number
                m = re.match(r'^(.{2,}?)\s+([\d,.\-()]{5,})\s*$', line)
                if m:
                    item = m.group(1).strip()
                    if re.search(r'[\u4e00-\u9fff]', item) and len(item) < 30:
                        rows.append([item, m.group(2).strip(), ""])
            return rows

        def _rows_to_md(rows: list[list[str]], col_headers: list[str]) -> str:
            if not rows:
                return "_未提取到数据_"
            lines = ["| " + " | ".join(col_headers) + " |"]
            lines.append("| " + " | ".join(["---"] * len(col_headers)) + " |")
            for r in rows:
                while len(r) < len(col_headers):
                    r.append("")
                lines.append("| " + " | ".join(r[:len(col_headers)]) + " |")
            return "\n".join(lines)

        # 利润表 (合并)
        income_pages = _find_table_pages(["合并利润表", "营业总收入"], ["公司利润表"])
        if income_pages:
            rows = _parse_table_rows(pages_text[income_pages[0]])
            if rows:
                result_parts.append(
                    "### 合并利润表\n\n"
                    + _rows_to_md(rows, ["项目", _y_cur, _y_prev])
                )

        # 资产负债表 (合并, may span 2 pages)
        balance_pages = _find_table_pages(
            ["合并资产负债表", "货币资金"], ["公司资产负债表"]
        )
        if balance_pages:
            all_rows = []
            for pg in balance_pages:
                all_rows.extend(_parse_table_rows(pages_text[pg]))
            if all_rows:
                result_parts.append(
                    "### 合并资产负债表\n\n"
                    + _rows_to_md(all_rows, ["项目", _y_end, _y_begin])
                )

        # 现金流量表 (合并)
        cf_pages = _find_table_pages(["合并现金流量表", "销售商品"], ["公司现金流量表"])
        if cf_pages:
            rows = _parse_table_rows(pages_text[cf_pages[0]])
            if rows:
                result_parts.append(
                    "### 合并现金流量表\n\n"
                    + _rows_to_md(rows, ["项目", _y_cur, _y_prev])
                )

        # --- 2. Extract 营业收入/成本明细 (附注54) ---
        for i, text in enumerate(pages_text):
            if "54、营业收入和营业成本" in text and "按商品类型分类" in text:
                # Extract the raw text between "54、营业收入" and the next section
                # to preserve all segment data (may have multi-line item names)
                seg_match = re.search(
                    r'(54、营业收入和营业成本.+?)(?:55、利息收入|56、税金)',
                    text, re.DOTALL,
                )
                if seg_match:
                    seg_text = seg_match.group(1).strip()
                    # Clean up: remove page headers and refs
                    seg_text = re.sub(
                        r'珠海格力电器股份有限公司\d+年年度报告全文\n?',
                        '', seg_text,
                    )
                    seg_text = re.sub(r'\d+\n$', '', seg_text.strip())
                    result_parts.append(
                        "### 营业收入和营业成本明细（附注54）\n\n"
                        "以下为原始数据，请据此计算各产品/地区的毛利率：\n\n"
                        f"```\n{seg_text}\n```"
                    )
                break

    if result_parts:
        return "\n\n".join(result_parts)

    return ""


def _extract_financial_section_fallback(text: str) -> str:
    """Fallback extraction using pdfminer text (no pdfplumber)."""
    if not text:
        return ""

    lines = text.split("\n")

    def _find_metric(keyword: str) -> str:
        for line in lines:
            if keyword in line:
                parts = line.split()
                for part in parts:
                    cleaned = part.strip()
                    if re.match(r'^[\d,.\-()]+$', cleaned) and len(cleaned) > 2:
                        return cleaned
        return ""

    sections = []

    income_keywords = [
        "营业收入合计", "营业支出合计", "营业利润", "利润总额",
        "净利润", "基本每股收益", "综合收益总额",
    ]
    income_metrics = []
    for kw in income_keywords:
        val = _find_metric(kw)
        if val:
            income_metrics.append(f"- {kw}: {val} 百万元")
    if income_metrics:
        sections.append("### 利润表\n**关键指标**:\n" + "\n".join(income_metrics))

    balance_keywords = [
        "资产总计", "负债合计", "股东权益合计", "负债及股东权益总计",
    ]
    balance_metrics = []
    for kw in balance_keywords:
        val = _find_metric(kw)
        if val:
            balance_metrics.append(f"- {kw}: {val} 百万元")
    if balance_metrics:
        sections.append("### 资产负债表\n**关键指标**:\n" + "\n".join(balance_metrics))

    cf_keywords = [
        "经营活动产生的现金流量净额", "投资活动使用的现金流量净额",
        "筹资活动使用的现金流量净额", "现金及现金等价物净增加",
    ]
    cf_metrics = []
    for kw in cf_keywords:
        val = _find_metric(kw)
        if val:
            cf_metrics.append(f"- {kw}: {val} 百万元")
    if cf_metrics:
        sections.append("### 现金流量表\n**关键指标**:\n" + "\n".join(cf_metrics))

    equity_keywords = [
        "股本", "其他权益工具", "资本公积", "其他综合收益",
        "盈余公积", "一般风险准备", "未分配利润", "股东权益合计",
    ]
    equity_metrics = []
    for kw in equity_keywords:
        val = _find_metric(kw)
        if val:
            equity_metrics.append(f"- {kw}: {val} 百万元")
    if equity_metrics:
        sections.append(
            "### 股东权益变动表\n**关键指标**:\n" + "\n".join(equity_metrics)
        )

    if sections:
        return "\n\n".join(sections)

    return ""


async def _search_report(
    client: httpx.AsyncClient, symbol: str, keyword: str
) -> dict | None:
    """Search cninfo for a specific report type."""
    payload = {
        "stock": "",
        "pageNum": "1",
        "pageSize": "10",
        "tabKey": "fulltext",
        "category": "",
        "seDate": "",
        "searchkey": f"{symbol} {keyword}",
        "isHLtitle": "true",
        "sortName": "announcementTime",
        "sortType": "desc",
    }

    try:
        resp = await client.post(_CNINFO_URL, data=payload, headers=_HEADERS)
        if resp.status_code != 200:
            return None

        data = resp.json()
        items = data.get("announcements", [])

        for item in items:
            if item.get("secCode") != symbol:
                continue
            title_raw = item.get("announcementTitle", "")
            title = re.sub(r"<[^>]+>", "", title_raw).strip()
            if "摘要" in title or "英文" in title:
                continue
            if keyword in title:
                adjunct_url = item.get("adjunctUrl", "")
                if adjunct_url:
                    pdf_url = f"https://static.cninfo.com.cn/{adjunct_url}"
                else:
                    pdf_url = ""
                year_match = re.search(r"(\d{4})\s*年", title)
                year = year_match.group(1) if year_match else ""
                return {
                    "year": year,
                    "title": title[:100],
                    "pdf_url": pdf_url,
                }
    except Exception as e:
        logging.warning("[cninfo_search_report] %s: %s", type(e).__name__, e)
    return None


async def _download_and_extract(
    client: httpx.AsyncClient, pdf_url: str
) -> str:
    """Download PDF and extract financial text."""
    if not pdf_url:
        return ""
    try:
        resp = await client.get(pdf_url, timeout=30.0)
        if resp.status_code != 200:
            return ""
        return extract_financial_data_from_pdf(resp.content)
    except Exception as e:
        logging.warning("[pdf_download_extract] %s: %s", type(e).__name__, e)
        return ""


async def fetch_reports(symbol: str, force: bool = False) -> dict:
    """Fetch latest reports, download PDFs, extract financial text.

    Returns dict with keys:
        symbol, annual, semi_annual, quarterly, cached
    """
    if not force:
        cached = _load_cache(symbol)
        if cached:
            cached["cached"] = True
            return cached

    t0 = time.monotonic()
    result: dict[str, Any] = {
        "symbol": symbol,
        "annual": None,
        "semi_annual": None,
        "quarterly": None,
        "cached": False,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            tasks = [
                _search_report(client, symbol, keyword)
                for keyword, _ in _REPORT_TYPES
            ]
            reports = await asyncio.gather(*tasks, return_exceptions=True)
            for (keyword, label), report in zip(_REPORT_TYPES, reports):
                if isinstance(report, Exception):
                    continue
                if report:
                    pdf_text = await _download_and_extract(
                        client, report.get("pdf_url", "")
                    )
                    report["content"] = pdf_text
                    if label == "年报":
                        result["annual"] = report
                    elif label == "半年报":
                        result["semi_annual"] = report
                    elif label == "季报":
                        result["quarterly"] = report
    except Exception as e:
        logging.warning("[annual_report_fetch_reports] %s: %s", type(e).__name__, e)

    elapsed = (time.monotonic() - t0) * 1000

    reports_found = []
    if result["annual"]:
        has_content = bool(result["annual"].get("content"))
        reports_found.append(
            f"年报{result['annual']['year']}({'有' if has_content else '无'}内容)"
        )
    if result["semi_annual"]:
        has_content = bool(result["semi_annual"].get("content"))
        label = "有" if has_content else "无"
        reports_found.append(
            f"半年报{result['semi_annual']['year']}({label}内容)"
        )
    if result["quarterly"]:
        has_content = bool(result["quarterly"].get("content"))
        reports_found.append(
            f"季报{result['quarterly']['year']}({'有' if has_content else '无'}内容)"
        )
    if reports_found:
        print(f"   报告获取: {', '.join(reports_found)} ({elapsed:.0f}ms)")
    else:
        print(f"   报告获取: 未找到 ({elapsed:.0f}ms)")

    if result["annual"] or result["semi_annual"] or result["quarterly"]:
        _save_cache(symbol, result)
    return result


def save_report_as_md(
    symbol: str, name: str, reports: dict, output_dir: str | None = None
) -> Path:
    """Save extracted financial report data as a markdown file.

    Includes 利润表/现金流量表/资产负债表/股东权益变动表 extracted from annual,
    semi-annual, and quarterly reports.

    Args:
        symbol: Stock code (e.g. "000001").
        name: Stock name (e.g. "平安银行").
        reports: Dict returned by fetch_reports().
        output_dir: Directory to save the file. Defaults to settings.output_dir.

    Returns:
        Path to the saved markdown file.
    """
    settings = get_settings()
    out_dir = Path(output_dir) if output_dir else settings.output_path
    out_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d")
    filename = f"{symbol}_{name}_财务数据_{date_str}.md"
    filepath = out_dir / filename

    lines: list[str] = []
    lines.append(f"# {name}({symbol}) 财务数据提取")
    lines.append(f"\n> 提取时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("\n> 包含报表: 利润表、资产负债表、现金流量表、股东权益变动表\n")

    has_data = False

    report_items = [
        ("annual", "年度报告"),
        ("semi_annual", "半年度报告"),
        ("quarterly", "季度报告"),
    ]

    for key, label in report_items:
        report = reports.get(key)
        if not report:
            continue

        has_data = True
        year = report.get("year", "")
        title = report.get("title", "")
        pdf_url = report.get("pdf_url", "")
        content = report.get("content", "")

        lines.append(f"\n---\n## {label} ({year}年)\n")
        lines.append(f"- **标题**: {title}")
        lines.append(f"- **PDF链接**: {pdf_url}\n")

        if content:
            lines.append(content)
            lines.append("")
        else:
            lines.append("_未提取到PDF内容_\n")

    if not has_data:
        lines.append("\n_未找到任何财务报告_\n")

    filepath.write_text("\n".join(lines), encoding="utf-8")
    logging.info("[save_report_as_md] 财务数据已保存: %s", filepath)
    return filepath
