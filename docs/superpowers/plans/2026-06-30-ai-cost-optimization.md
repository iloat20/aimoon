# AI 分析成本优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 降低 AI 分析单次成本 40%，并支持缓存复用

**Architecture:** 精简系统提示词 + 压缩数据格式 + DiskTtlCache 缓存分析结果

**Tech Stack:** Python, DeepSeek API, DiskTtlCache

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `src/aimoon/adapters/driven/ai/prompts.py` | 修改 | 精简系统提示词 |
| `src/aimoon/adapters/driven/ai/analyzer.py` | 修改 | 数据压缩 + 缓存逻辑 |
| `src/aimoon/adapters/driven/common/cache.py` | 只读 | 已有 DiskTtlCache |
| `tests/test_ai_cost_optimization.py` | 新建 | 缓存和数据压缩测试 |

---

### Task 1: 精简系统提示词

**Files:**
- Modify: `src/aimoon/adapters/driven/ai/prompts.py:8-78`

- [ ] **Step 1: 备份当前提示词**

记录当前 SYSTEM_PROMPT 的 token 数（用于对比）：

```bash
uv run python -c "
from aimoon.adapters.driven.ai.prompts import SYSTEM_PROMPT
print(f'Current SYSTEM_PROMPT: {len(SYSTEM_PROMPT)} chars')
"
```

- [ ] **Step 2: 替换为精简版提示词**

将 `prompts.py` 中的 `SYSTEM_PROMPT` 替换为以下内容：

```python
SYSTEM_PROMPT = """\
你是一名逆向投资研究员，注重安全边际和独立判断。

**分析框架（四部分）：**

### 第一部分：业务画像与护城河
1. 核心业务：一句话定义公司，表格列出分产品/分地区收入（金额、占比、增速）。
2. 商业模式：盈利模式 + 对上下游议价能力（强/中/弱）。
3. 竞争格局：行业规模及增速（注明出处），表格对比3+竞争对手。
4. 增长与风险：量化增长点（收入增量估算），指出3+隐蔽风险。

### 第二部分：财务健康诊断
1. 成长性：三年营收/净利润趋势，ROE变动驱动因素。
2. 现金流：经营现金流/净利润比，自由现金流覆盖能力。
3. 资产负债：负债率、有息负债率、关键比率异常检查。
4. 评级：优/良/差/高危 + 三条依据。

### 第三部分：交叉验证
业务能力 vs 财务结果，寻找背离。最终判断：价值创造型/成长消耗型/价值陷阱型。

### 第四部分：估值与逆向视角
1. 估值：PE/PB + FCFE模型 + 保守/中性/乐观目标价 + 同业对比表格。
2. 看空理由：三个最强做空逻辑（含量化风险情景）。

**规则：**
- 数据来源标注：公司年报/搜索结果/训练数据。
- 每个结论给三个理由 + 支撑度占比。
- 表格只放数据，结论放表后。
- 全文中文。"""
```

- [ ] **Step 3: 验证提示词长度**

```bash
uv run python -c "
from aimoon.adapters.driven.ai.prompts import SYSTEM_PROMPT
print(f'New SYSTEM_PROMPT: {len(SYSTEM_PROMPT)} chars')
assert len(SYSTEM_PROMPT) < 1800, f'Too long: {len(SYSTEM_PROMPT)}'
print('OK: prompt shortened')
"
```

- [ ] **Step 4: 运行测试确保无破坏**

```bash
uv run pytest tests/test_ai.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/aimoon/adapters/driven/ai/prompts.py
git commit -m "perf: simplify system prompt to reduce input tokens"
```

---

### Task 2: 数据压缩

**Files:**
- Modify: `src/aimoon/adapters/driven/ai/analyzer.py:495-525`

- [ ] **Step 1: 写测试验证数据压缩**

新建 `tests/test_ai_cost_optimization.py`：

```python
"""Tests for AI analysis cost optimization."""
from datetime import datetime


def test_compressed_financial_keys():
    """Financial data should use short keys."""
    from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer

    analyzer = DeepSeekAIAnalyzer(mock=True)
    from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
    from aimoon.core.domain.entities.financial import FinancialData

    stock = StockAnalysis(
        symbol="600519",
        name="贵州茅台",
        financial=FinancialData(
            symbol="600519",
            revenue=171118000000,
            revenue_yoy=15.5,
            net_profit=86220000000,
            net_profit_yoy=12.3,
            roe=33.59,
            total_assets=250000000000,
            total_liabilities=80000000000,
            operating_cf=50000000000,
        ),
    )
    data = analyzer._build_data_dict(stock)
    fin = data["financial"]
    # Should use short keys
    assert "rev" in fin
    assert "np" in fin
    assert "roe" in fin
    # Should not have Chinese keys
    assert "营收(亿)" not in fin
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_ai_cost_optimization.py -v
```

- [ ] **Step 3: 修改 `_build_data_dict` 使用短 key**

在 `analyzer.py` 的 `_build_data_dict` 方法中，将 financial 字典的 key 替换：

```python
"financial": {
    "rev": (round(financial.revenue / 1e8, 2) if financial.revenue else 0),
    "rev_yoy": financial.revenue_yoy,
    "np": (round(financial.net_profit / 1e8, 2) if financial.net_profit else 0),
    "np_yoy": financial.net_profit_yoy,
    "roe": financial.roe,
    "eps": financial.eps,
    "ta": (round(financial.total_assets / 1e8, 2) if financial.total_assets else 0),
    "tl": (round(financial.total_liabilities / 1e8, 2) if financial.total_liabilities else 0),
    "ocf": (round(financial.operating_cf / 1e8, 2) if financial.operating_cf else 0),
    "period": financial.report_period,
},
```

同样压缩 quarterly_financial：

```python
"quarterly_financial": {
    "period": quarterly.report_period,
    "type": quarterly.report_type,
    "rev": (round(quarterly.revenue / 1e8, 2) if quarterly.revenue else 0),
    "rev_yoy": quarterly.revenue_yoy,
    "np": (round(quarterly.net_profit / 1e8, 2) if quarterly.net_profit else 0),
    "np_yoy": quarterly.net_profit_yoy,
},
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_ai_cost_optimization.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/aimoon/adapters/driven/ai/analyzer.py tests/test_ai_cost_optimization.py
git commit -m "perf: compress data keys to reduce input tokens"
```

---

### Task 3: 过滤零值字段

**Files:**
- Modify: `src/aimoon/adapters/driven/ai/analyzer.py:507-525`

- [ ] **Step 1: 写测试**

在 `tests/test_ai_cost_optimization.py` 中添加：

```python
def test_zero_fields_excluded():
    """Fields with value 0 should not appear in data dict."""
    from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer

    analyzer = DeepSeekAIAnalyzer(mock=True)
    from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
    from aimoon.core.domain.entities.financial import FinancialData

    stock = StockAnalysis(
        symbol="600519",
        name="贵州茅台",
        financial=FinancialData(symbol="600519"),  # all zeros
    )
    data = analyzer._build_data_dict(stock)
    fin = data["financial"]
    # Zero fields should be excluded or set to None
    for key, val in fin.items():
        if key == "period":
            continue
        assert val != 0, f"Zero field '{key}' should be excluded"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_ai_cost_optimization.py::test_zero_fields_excluded -v
```

- [ ] **Step 3: 修改 `_build_data_dict` 过滤零值**

在构建 financial 字典时，用条件表达式排除零值：

```python
fin_dict = {}
if financial.revenue:
    fin_dict["rev"] = round(financial.revenue / 1e8, 2)
if financial.revenue_yoy:
    fin_dict["rev_yoy"] = financial.revenue_yoy
if financial.net_profit:
    fin_dict["np"] = round(financial.net_profit / 1e8, 2)
if financial.net_profit_yoy:
    fin_dict["np_yoy"] = financial.net_profit_yoy
if financial.roe:
    fin_dict["roe"] = financial.roe
if financial.eps:
    fin_dict["eps"] = financial.eps
if financial.total_assets:
    fin_dict["ta"] = round(financial.total_assets / 1e8, 2)
if financial.total_liabilities:
    fin_dict["tl"] = round(financial.total_liabilities / 1e8, 2)
if financial.operating_cf:
    fin_dict["ocf"] = round(financial.operating_cf / 1e8, 2)
fin_dict["period"] = financial.report_period
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_ai_cost_optimization.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/aimoon/adapters/driven/ai/analyzer.py tests/test_ai_cost_optimization.py
git commit -m "perf: exclude zero-value fields from AI prompt data"
```

---

### Task 4: 社交舆情精简

**Files:**
- Modify: `src/aimoon/adapters/driven/ai/analyzer.py:460-482`

- [ ] **Step 1: 写测试**

在 `tests/test_ai_cost_optimization.py` 中添加：

```python
def test_social_posts_limited_to_top5():
    """Social posts should be limited to top 5 per platform."""
    from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer

    analyzer = DeepSeekAIAnalyzer(mock=True)
    from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
    from aimoon.core.domain.entities.social import SocialPost

    posts = [
        SocialPost(
            platform="东方财富股吧",
            url=f"http://example.com/{i}",
            title=f"帖子{i}",
            likes=100 - i,
        )
        for i in range(20)
    ]
    stock = StockAnalysis(symbol="600519", name="贵州茅台", social_posts=posts)
    data = analyzer._build_data_dict(stock)
    # Should only have top 5
    eastmoney = data["eastmoney"]
    assert eastmoney.count("- ") <= 5, f"Expected <=5 posts, got {eastmoney.count('- ')}"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_ai_cost_optimization.py::test_social_posts_limited_to_top5 -v
```

- [ ] **Step 3: 修改社交数据收集逻辑**

在 `_build_data_dict` 中，对每个平台的帖子按 likes 排序后只取 top5：

```python
for p in info.social_posts:
    line = f"- {p.title} (赞{p.likes} 评{p.comments})"
    plat = p.platform
    if "雪球" in plat:
        texts["xueqiu"].append(line)
    elif "股吧" in plat:
        texts["eastmoney"].append(line)
    elif "头条" in plat:
        texts["toutiao"].append(line)
    elif "微信" in plat or "公众号" in plat:
        texts["wechat"].append(line)

# Sort by likes descending and take top 5
for key in texts:
    texts[key] = texts[key][:5]
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_ai_cost_optimization.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/aimoon/adapters/driven/ai/analyzer.py tests/test_ai_cost_optimization.py
git commit -m "perf: limit social posts to top 5 per platform"
```

---

### Task 5: AI 分析缓存

**Files:**
- Modify: `src/aimoon/adapters/driven/ai/analyzer.py:105-128`
- Create: `src/aimoon/adapters/driven/ai/cache.py`

- [ ] **Step 1: 创建缓存封装**

新建 `src/aimoon/adapters/driven/ai/cache.py`：

```python
"""AI analysis cache using DiskTtlCache."""

from __future__ import annotations

from datetime import datetime

from aimoon.adapters.driven.common.cache import DiskTtlCache

_cache = DiskTtlCache(namespace="ai_analysis", ttl_seconds=86400)


def get_analysis_cache(symbol: str) -> str | None:
    """Get cached analysis for symbol if fresh enough."""
    today = datetime.now().strftime("%Y%m%d")
    key = f"analysis:{symbol}:{today}"
    cached = _cache.get(key)
    if cached and isinstance(cached, dict):
        return cached.get("report_text")
    return None


def set_analysis_cache(symbol: str, report_text: str) -> None:
    """Cache analysis result."""
    today = datetime.now().strftime("%Y%m%d")
    key = f"analysis:{symbol}:{today}"
    # 财报季缩短 TTL
    month = datetime.now().month
    ttl = 21600 if month in (1, 4, 7, 10) else 86400
    _cache.ttl_seconds = ttl
    _cache.set(key, {"report_text": report_text})
```

- [ ] **Step 2: 写测试**

在 `tests/test_ai_cost_optimization.py` 中添加：

```python
def test_analysis_cache_hit():
    """Cached analysis should be returned on cache hit."""
    from aimoon.adapters.driven.ai.cache import get_analysis_cache, set_analysis_cache

    set_analysis_cache("TEST01", "cached report")
    result = get_analysis_cache("TEST01")
    assert result == "cached report"


def test_analysis_cache_miss():
    """Different symbols should not share cache."""
    from aimoon.adapters.driven.ai.cache import get_analysis_cache

    result = get_analysis_cache("NONEXISTENT")
    assert result is None
```

- [ ] **Step 3: 运行测试确认通过**

```bash
uv run pytest tests/test_ai_cost_optimization.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/aimoon/adapters/driven/ai/cache.py tests/test_ai_cost_optimization.py
git commit -m "feat: add AI analysis cache with daily TTL"
```

---

### Task 6: 集成缓存到分析流程

**Files:**
- Modify: `src/aimoon/adapters/driven/ai/analyzer.py:105-128`

- [ ] **Step 1: 写测试**

在 `tests/test_ai_cost_optimization.py` 中添加：

```python
def test_analyzer_uses_cache():
    """Analyzer should return cached result when available."""
    from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer
    from aimoon.adapters.driven.ai.cache import set_analysis_cache
    from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis

    set_analysis_cache("CACHED01", "cached analysis report")
    analyzer = DeepSeekAIAnalyzer(mock=True)
    stock = StockAnalysis(symbol="CACHED01", name="测试")
    report = analyzer._analyze_stock(stock)
    assert report.report_text == "cached analysis report"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_ai_cost_optimization.py::test_analyzer_uses_cache -v
```

- [ ] **Step 3: 修改 analyzer 集成缓存**

在 `analyzer.py` 的 `analyze` 方法中，先检查缓存：

```python
async def analyze(
    self,
    stock_info: StockAnalysis,
    reports: dict | None = None,
    financial_md_path: Path | None = None,
) -> AnalysisReport:
    """AI analysis entry point."""
    if self._mock:
        from ..collectors.mock import mock_analysis_report
        return mock_analysis_report(stock_info.symbol, stock_info.name)

    # 检查缓存
    from .cache import get_analysis_cache
    cached = get_analysis_cache(stock_info.symbol)
    if cached:
        return AnalysisReport(
            symbol=stock_info.symbol,
            name=stock_info.name,
            summary=cached[:200] + "..." if len(cached) > 200 else cached,
            report_text=cached,
            investment_advice="本报告由DeepSeek AI自动生成，仅供参考，不构成投资建议。",
        )

    collected_data = self._build_data_dict(stock_info, reports, financial_md_path)
    try:
        md = await self._call_deepseek(stock_info.symbol, stock_info.name, collected_data)
        md = _deduplicate_tail(md)
    except Exception as e:
        logging.warning("[ai_analyze_stock] %s: %s", type(e).__name__, e)
        md = "AI分析暂不可用，以下为基础数据汇总。"

    # 写入缓存
    from .cache import set_analysis_cache
    set_analysis_cache(stock_info.symbol, md)

    short = md[:200]
    short = re.sub(r"\*\*(.*?)\*\*", r"\1", short)
    short = re.sub(r"##?\s*", "", short)
    short = re.sub(r"\* ", "• ", short)
    if len(md) > 200:
        short += "..."

    result = AnalysisReport(
        symbol=stock_info.symbol,
        name=stock_info.name,
        summary=short,
        report_text=md,
        investment_advice="本报告由DeepSeek AI自动生成，仅供参考，不构成投资建议。",
    )
    result = self._sanitize_support_resistance(
        result, stock_info.quote.price if stock_info.quote else None
    )
    return result
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_ai_cost_optimization.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/aimoon/adapters/driven/ai/analyzer.py
git commit -m "perf: integrate AI analysis cache into analyzer"
```

---

### Task 7: 全量验证

- [ ] **Step 1: 运行全部测试**

```bash
uv run pytest tests/ -v
```

- [ ] **Step 2: 运行 lint 和类型检查**

```bash
uv run ruff check src/
uv run mypy src/aimoon/
```

- [ ] **Step 3: 实际运行验证**

```bash
uv run aimoon test 000651
```

- [ ] **Step 4: 验证缓存生效（第二次运行应更快）**

```bash
uv run aimoon test 000651
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "perf: complete AI analysis cost optimization"
```
