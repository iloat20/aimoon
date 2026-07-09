# AI 分析质量全面优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提升 AI 分析的准确性、深度、实用性和灵活性

**Architecture:** 增强系统提示词 + 增强数据格式（添加来源标注和行业信息）

**Tech Stack:** Python, DeepSeek API

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `src/aimoon/adapters/driven/ai/prompts.py` | 修改 | 增强系统提示词 |
| `src/aimoon/adapters/driven/ai/analyzer.py` | 修改 | 增强数据格式 |

---

### Task 1: 增强系统提示词 — 准确性和深度

**Files:**
- Modify: `src/aimoon/adapters/driven/ai/prompts.py:8-40`

- [ ] **Step 1: 替换系统提示词**

将 `prompts.py` 中的 `SYSTEM_PROMPT` 替换为以下内容：

```python
SYSTEM_PROMPT = """\
你是一名逆向投资研究员，注重安全边际和独立判断。你必须基于用户提供的数据进行分析，不得编造数字。

**分析框架（四部分）：**

### 第一部分：业务画像与护城河
1. 核心业务：一句话定义公司。表格列出分产品/分地区收入（金额、占比、增速）。
2. 商业模式：盈利模式 + 对上下游议价能力（强/中/弱）。分析核心竞争力来源和可持续性。
3. 竞争格局：行业规模及增速（注明出处），表格对比3+竞争对手。分析各公司相对位势变化。
4. 增长与风险：量化增长点（收入增量估算），指出3+隐蔽风险（含触发条件和潜在冲击）。

### 第二部分：财务健康诊断
1. 成长性：三年营收/净利润趋势，ROE变动驱动因素（利润率/周转/杠杆）。分析变动原因。
2. 现金流：经营现金流/净利润比（低于0.7需警示），自由现金流覆盖能力。判断利润含金量。
3. 资产负债：负债率、有息负债率、关键比率异常检查（应收账款/营收、商誉/净资产、存货/营业成本）。
4. 评级：优/良/差/高危 + 三条依据。

### 第三部分：交叉验证
业务能力 vs 财务结果，寻找背离。最终判断：价值创造型/成长消耗型/价值陷阱型。

### 第四部分：估值与逆向视角
1. 估值：PE/PB + FCFE模型 + 保守/中性/乐观目标价（基于不同增速假设）+ 同业对比表格。
2. 看空理由：三个最强做空逻辑（含量化风险情景和对估值的影响）。
3. **投资建议**：明确给出买入/持有/卖出建议 + 价格区间 + 催化剂事件 + 持有期限。

**规则：**
- **数据准确性**：只使用用户提供的数据和搜索结果，不得编造数字。如果数据不足，明确说明"数据缺失"而非猜测。
- **来源标注**：每个关键数字标注来源（公司年报/搜索结果/训练数据）。
- 每个结论给三个理由 + 支撑度占比。
- **表格格式**：必须使用标准 Markdown 表格语法（`| 列1 | 列2 |` + `|---|---|` 分隔行）。
  - 表格只放关键数字，不要放计算过程、假设条件或长文本。
  - 每个表格不超过 6 行数据。
  - 结论和分析判断放在表格之后，不要写在表格单元格内。
- **严禁重复**：每个结论只写一次，不要重复输出相同内容。
- 全文中文。"""
```

- [ ] **Step 2: 验证提示词长度**

```bash
uv run python -c "
from aimoon.adapters.driven.ai.prompts import SYSTEM_PROMPT
print(f'SYSTEM_PROMPT: {len(SYSTEM_PROMPT)} chars')
assert len(SYSTEM_PROMPT) < 2000, f'Too long: {len(SYSTEM_PROMPT)}'
print('OK')
"
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/test_ai.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/aimoon/adapters/driven/ai/prompts.py
git commit -m "feat: enhance system prompt for accuracy and depth"
```

---

### Task 2: 增强数据格式 — 添加来源标注

**Files:**
- Modify: `src/aimoon/adapters/driven/ai/analyzer.py:506-535`

- [ ] **Step 1: 修改 financial 字典添加来源**

在 `_build_data_dict` 方法中，修改 financial 字典的构建，添加来源标注：

```python
"financial": {
    **(
        {"rev": round(financial.revenue / 1e8, 2)}
        if financial.revenue
        else {}
    ),
    **({"rev_yoy": financial.revenue_yoy} if financial.revenue_yoy else {}),
    **(
        {"np": round(financial.net_profit / 1e8, 2)}
        if financial.net_profit
        else {}
    ),
    **({"np_yoy": financial.net_profit_yoy} if financial.net_profit_yoy else {}),
    **({"roe": financial.roe} if financial.roe else {}),
    **({"eps": financial.eps} if financial.eps else {}),
    **(
        {"ta": round(financial.total_assets / 1e8, 2)}
        if financial.total_assets
        else {}
    ),
    **(
        {"tl": round(financial.total_liabilities / 1e8, 2)}
        if financial.total_liabilities
        else {}
    ),
    **(
        {"ocf": round(financial.operating_cf / 1e8, 2)}
        if financial.operating_cf
        else {}
    ),
    "period": financial.report_period,
    "src": financial.source,
},
```

- [ ] **Step 2: 验证数据格式**

```bash
uv run python -c "
from aimoon.adapters.driven.ai.analyzer import DeepSeekAIAnalyzer
from aimoon.core.domain.aggregates.stock_analysis import StockAnalysis
from aimoon.core.domain.entities.financial import FinancialData

analyzer = DeepSeekAIAnalyzer(mock=True)
stock = StockAnalysis(
    symbol='600519',
    name='贵州茅台',
    financial=FinancialData(
        symbol='600519',
        revenue=171118000000,
        net_profit=86220000000,
        roe=33.59,
        source='akshare(2025年报)',
    ),
)
data = analyzer._build_data_dict(stock)
fin = data['financial']
print(f'Financial keys: {list(fin.keys())}')
assert 'src' in fin, 'Missing src key'
assert fin['src'] == 'akshare(2025年报)'
print('OK: source annotation present')
"
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/test_ai.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/aimoon/adapters/driven/ai/analyzer.py
git commit -m "feat: add source annotations to financial data"
```

---

### Task 3: 增强数据格式 — 添加行业信息

**Files:**
- Modify: `src/aimoon/adapters/driven/ai/analyzer.py:575-585`

- [ ] **Step 1: 修改返回字典添加行业信息**

在 `_build_data_dict` 方法的返回字典中，在 `"kline_summary"` 之前添加行业信息：

```python
    "industry": _detect_industry(stock_info.symbol, stock_info.name),
    "kline_summary": getattr(info, "kline_summary", None),
```

- [ ] **Step 2: 添加行业检测函数**

在 `analyzer.py` 文件顶部（`_MAX_TOOL_ROUNDS = 0` 之后）添加行业检测函数：

```python
# 行业关键词映射
_INDUSTRY_KEYWORDS = {
    "银行": ["银行", "工商银行", "建设银行", "农业银行", "招商银行", "兴业银行"],
    "地产": ["地产", "万科", "保利", "恒大", "碧桂园", "融创"],
    "消费": ["茅台", "五粮液", "泸州老窖", "伊利", "蒙牛", "海天"],
    "家电": ["格力", "美的", "海尔", "海信", "TCL", "长虹"],
    "科技": ["华为", "小米", "联想", "中兴", "立讯", "歌尔"],
    "医药": ["恒瑞", "药明", "迈瑞", "片仔癀", "云南白药"],
    "能源": ["中石油", "中石化", "中海油", "神华", "宁德时代"],
    "汽车": ["比亚迪", "长城", "吉利", "蔚来", "小鹏", "理想"],
}


def _detect_industry(symbol: str, name: str) -> str:
    """根据公司名称检测行业。"""
    for industry, keywords in _INDUSTRY_KEYWORDS.items():
        for kw in keywords:
            if kw in name:
                return industry
    # 默认根据股票代码前缀判断市场
    if symbol.startswith("6"):
        return "沪市"
    elif symbol.startswith(("0", "3")):
        return "深市"
    else:
        return "北交所"
```

- [ ] **Step 3: 验证行业检测**

```bash
uv run python -c "
from aimoon.adapters.driven.ai.analyzer import _detect_industry
assert _detect_industry('000651', '格力电器') == '家电'
assert _detect_industry('600519', '贵州茅台') == '消费'
assert _detect_industry('601398', '工商银行') == '银行'
print('OK: industry detection works')
"
```

- [ ] **Step 4: 运行测试**

```bash
uv run pytest tests/test_ai.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/aimoon/adapters/driven/ai/analyzer.py
git commit -m "feat: add industry detection for context-aware analysis"
```

---

### Task 4: 全量验证

- [ ] **Step 1: 运行全部检查**

```bash
uv run ruff check src/
uv run mypy src/aimoon/
uv run pytest tests/ -q
```

- [ ] **Step 2: 实际运行验证**

```bash
uv run python -c "from aimoon.adapters.driven.ai.cache import _cache; _cache.clear()"
uv run aimoon 000651
```

- [ ] **Step 3: 检查报告质量**

查看生成的报告，确认：
- 表格格式正确
- 数字有来源标注
- 结论有具体投资建议
- 无重复内容

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: complete AI analysis quality optimization"
```
