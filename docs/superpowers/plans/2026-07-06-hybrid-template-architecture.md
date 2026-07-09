# 混合模板+LLM 分析架构 — 实施计划

## 目标
- 三张核心表格(财务时序/同业/估值)由 Python 模板渲染,0 LLM token
- LLM 只生成分析文本(~500 tokens),不重复表格中已有数字
- DeepSeek thinking mode 默认 balanced(500),`--deep` flag → max(2000)
- 单次成本目标:$0.013(-54%),耗时目标:30s(-59%)

## 当前状态
- 提交 f171bec + subagent G 改动
- 单次成本 $0.028,73s,8/8 章节
- 系统提示 131 chars(已压缩)
- 但 user message 仍含完整 tool JSON,模型输出含重复表格

## 实施步骤

### 步骤 1:新增 `table_renderer.py`
路径:`src/aimoon/adapters/driven/ai/pipeline/table_renderer.py`

职责:把 tool_results 中的 JSON 渲染为 Markdown 表格。

需要渲染的 3 张表:
1. **财务时序表**(来自 `financial_temporal.years`)
   - 列:报告期/营收/营收同比/净利润/净利同比/ROE/EPS/经营现金流
   - 行:years 列表中的每年

2. **同业对比表**(来自 `peer_compare.peers`)
   - 列:公司/最新价/PE/PB/ROE/营收增速/净利增速/市值
   - 行:peers 列表中的每家公司

3. **估值三档表**(来自 `valuation.fcfe_targets` + `assumptions`)
   - 列:档位/PE/目标价/概率
   - 行:保守/中性/乐观

每张表函数签名:
```python
def render_financial_temporal(data: dict) -> str: ...
def render_peer_comparison(data: dict) -> str: ...
def render_valuation_targets(data: dict) -> str: ...
```

每个函数:
- 输入:tool JSON
- 输出:标准 Markdown 表格字符串(含表头+分隔行+数据行)
- 缺失字段用 "N/A" 填充
- 数字格式:亿为单位,保留 1 位小数;百分比保留 1 位小数

### 步骤 2:重写 `analysis.md` system prompt
路径:`src/aimoon/adapters/driven/ai/pipeline/prompts/analysis.md`

新内容(≤500 chars):
```
逆向投资研究员。基于下方【已渲染表格】+【工具摘要】,撰写 1500-2000 字分析。

⚠️ 禁止重复表格中已有数字。只做:对比+判断+风险触发条件。

章节(按此顺序,不可省略):
## 一、业务画像与护城河(300-400 字)
## 二、财务健康诊断(300-400 字)
## 三、交叉验证(200-300 字)
## 四、风险量化与逆向视角(300-400 字)
## 五、投资建议(200-300 字)

末尾 1 行 JSON(无换行):
{"citations_ok":bool,"tables_ok":bool,"trigger_ok":bool,"advice_ok":bool,"financial_depth_ok":bool,"business_depth_ok":bool,"norepeat_ok":bool,"justified_ok":bool,"fixes_needed":[]}
false 时 fixes_needed 必须列具体修复点(≤40 字/条)。
```

### 步骤 3:修改 `orchestrator.py` 的 user message 组装
路径:`src/aimoon/adapters/driven/ai/pipeline/orchestrator.py`

在 `_phase_analysis` 方法中,修改 messages 组装逻辑:

**当前**(伪代码):
```python
system = phase_system_prompt(Phase.ANALYSIS, stock_md, {"tools_output": tool_results})
injected = _tool_results_to_messages(tool_results)  # 完整 JSON
messages = [
    {"role": "system", "content": system},
    {"role": "user", "content": stock_md},
    *injected,
]
```

**新**(伪代码):
```python
from .table_renderer import render_financial_temporal, render_peer_comparison, render_valuation_targets

# 1. 渲染表格
tables_md = "\n\n".join([
    render_financial_temporal(tool_results.get("financial_temporal", {})),
    render_peer_comparison(tool_results.get("peer_compare", {})),
    render_valuation_targets(tool_results.get("valuation", {})),
])

# 2. 提取非表格摘要
summary = _extract_tool_summary(tool_results)  # 趋势/资金/RSI/看空逻辑/护城河

# 3. 组装 user message
user_content = f"""# 标的快照
{stock_md}

# 已渲染表格(禁止重复其中数字)
{tables_md}

# 工具摘要(非表格部分)
{summary}

# 分析指令
见 system prompt。"""

system = phase_system_prompt(Phase.ANALYSIS, stock_md, {})  # 不再传 tools_output
messages = [
    {"role": "system", "content": system},
    {"role": "user", "content": user_content},
]
```

新增辅助函数 `_extract_tool_summary(tool_results) -> str`:
- 提取 technicals.trend, main_net_5d, RSI
- 提取 risk_quant.bears(触发条件+冲击%,不含 recommendation)
- 提取 business_moat.moat_sources, ocf_quality
- 输出 ~200 chars 的纯文本摘要

### 步骤 4:修改 self-check JSON schema
路径:`src/aimoon/adapters/driven/ai/pipeline/orchestrator.py`

在 `_REQUIRED_GATES` 中:
- 保留 `tables_ok`(但含义变为"模型没有重复表格数字")
- 保留其他 5 项

在 `_run_self_check` 的 prompt 中:
- 添加检测逻辑:"模型输出中是否直接复制了表格中的数字?如果有, tables_ok=false"

### 步骤 5:新增 `--deep` flag
路径:`src/aimoon/adriving/cli/main.py` + `orchestrator.py`

CLI:
```python
parser.add_argument(
    "--deep", action="store_true",
    help="深度分析模式:thinking budget 调到 2000,分析更深入但更慢更贵"
)
```

Orchestrator:
```python
async def run(self, si, ..., deep: bool = False):
    ...
    thinking_budget = 2000 if deep else 500  # 单 call 模式
```

### 步骤 6:保留旧路径作为 fallback
- 新增 `--legacy-2phase` flag → 使用旧的 2-phase 模式(完整 JSON 注入)
- 默认使用新混合模式

## 验证计划

### 单元测试
- `tests/test_table_renderer.py`:测试 3 个渲染函数
  - 正常输入 → 标准 Markdown 表格
  - 缺失字段 → "N/A" 填充
  - 空输入 → 空字符串或最小表格

### 集成测试
- `tests/test_pipeline_phases.py` 扩展:
  - `test_hybrid_mode_produces_no_duplicated_numbers`:验证输出不包含表格中的数字
  - `test_deep_mode_uses_higher_thinking_budget`:验证 deep flag 传递

### 实盘验证
- `uv run aimoon 000651` → 预期 30s,$0.013,8/8 章节
- `uv run aimoon 000651 --deep` → 预期 60s,$0.04,8/8 章节(更深度)
- `uv run aimoon 000651 --legacy-2phase` → 旧路径 fallback

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 表格渲染与 LLM 分析脱节 | user message 保留工具摘要(趋势/资金/RSI) |
| 模型不遵循"不重复表格数字" | prompt 明确禁止 + self-check 检测重复 |
| 新架构引入 bug | 保留 `--legacy-2phase` fallback |
| 表格格式不美观 | 使用标准 Markdown 表头+分隔行+对齐 |

## 关键文件清单

| 文件 | 操作 |
|---|---|
| `pipeline/table_renderer.py` | **新建** |
| `pipeline/prompts/analysis.md` | 重写 |
| `pipeline/orchestrator.py` | 修改 user message 组装 + 添加 `_extract_tool_summary` |
| `driving/cli/main.py` | 添加 `--deep` flag |
| `driving/cli/pipeline.py` | 传递 `deep` 参数 |
| `tests/test_table_renderer.py` | **新建** |
| `tests/test_pipeline_phases.py` | 扩展测试 |

## 时间估算

| 步骤 | 时间 |
|---|---|
| 1. table_renderer.py | 1h |
| 2. analysis.md 重写 | 30 min |
| 3. orchestrator.py 修改 | 1.5h |
| 4. self-check 调整 | 30 min |
| 5. --deep flag | 30 min |
| 6. 测试 + 实盘 | 1.5h |
| **总计** | **5h** |

## 预期收益

| 指标 | 当前 | 重构后 | 节省 |
|---|---|---|---|
| LLM 输出 tokens | 1,331 | **~500** | **-62%** |
| LLM 输入 tokens | 725 | ~550 | -24% |
| 单次成本 | $0.028 | **~$0.013** | **-54%** |
| 单次耗时 | 73s | **~30s** | -59% |
| 质量 | 8/8 | 8/8(表格更精确) | ↑ |
