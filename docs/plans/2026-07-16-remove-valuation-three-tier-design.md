# 设计文档：移除「估值三档表」（含估值工具三档计算）

日期：2026-07-16
范围：aimoon AI 分析 pipeline 的估值章重构

## 背景与决策

用户对生产报告（000651 格力电器）审查后要求：移除「估值三档表」（保守/中性/乐观目标价）。
经 4 轮范围确认，最终决策：

1. **范围 = 全删（含估值工具）**：不仅删渲染表格，也删 `valuation` 工具的三档价格计算。
2. **替代 = 只要安全边际指标**：报告不输出任何目标价。估值章改为「估值安全边际」单一结论。
3. **压力测试 = 后端确定性计算**：新增后端算「净利跌至 X 亿 → EPS → 股价」，根治之前
   AI 自由重算「150亿 → 13元（应为 20.6 元）」的一级 BUG。
4. **连隐含增速 g\* 也删**：不保留 Gordon 反推 g\* 护栏（尽管历史上有防符号算错的价值），
   估值章只保留静态安全边际指标。

## 改动清单

### 删除
- `tools/valuation.py`：
  - `fcfe_targets` 三档输出、`_project_fcfe` / `_project_ddm` / `_pv_fcfe` / `_none_targets`
  - 常量 `DISCOUNT_RATE` / `TERMINAL_GROWTH` / `FCFE_YEARS`
  - `implied_growth` / `_implied_growth_from` / `_discount_rate`（Gordon 反推）
- `tools/scenario_prob.py`（整个文件，依赖三档价格）
- `table_renderer.py`：`render_valuation_targets` / `render_scenario_prob` / `render_implied_growth`
- `orchestrator.py`：scenario 任务、`TOOL_RUNNERS["scenario_prob"]` 导入与调用、`render_*` 旧三调用
- `skeleton_schema.py`：`Valuation.targets`（`ValuationTargets`）、`sensitivity`
- `skeleton_renderer.py`：估值块的三档/ g\* / sensitivity 渲染
- `skeleton_validator.py`：估值目标完整性检查（#5）
- `tool_summaries.py`：`scenario_summary` 函数及调用
- `_helpers.py`：`target_base` 事实、`implied_growth` 事实、`_valuation_consistency_flags`（g\* 检查）
- 相关测试文件

### 新增 / 改造
- `tools/margin_of_safety.py`（新模块，注册 key 仍用 `"valuation"` 以减少联动）：
  ```python
  def run(fin_temporal, quote, peer_comp, financial=None) -> dict:
      # -> pe, pb,
      #    net_cash_pe = (market_cap - monetary_funds) / net_profit,
      #    peer_pe_median,
      #    stress: [{drop, net_profit, eps, price, downside_pct} × (-0.30, -0.50)]
  ```
  - 压力测试确定性公式：`eps = stress_net_profit / total_shares`；`price = eps × current_pe`；
    `downside_pct = (price - current_price) / current_price`。
  - 缺失数据（`monetary_funds`/`net_profit`/`market_cap`/`total_shares`）对应项置 `None` → 渲染 N/A。
- `table_renderer.render_margin_of_safety(val)`：单表 `## 估值安全边际`
  （当前 PE/PB、净现金调整 PE、同业 PE 中位数、两档压力测试下行空间）。
- `orchestrator.py`：valuation 改为 standalone（不再 await 后启 scenario）；`tables_md` 用
  `render_margin_of_safety(val)` 单行替换旧三调用；`tool_results` 去掉 `scenario_prob`。
- `skeleton_schema.Valuation`：保留 `peer_pe_median` / `expectation_gap`，新增 `net_cash_pe` / `stress`，
  删除 `targets` / `implied_g` / `sensitivity`。
- `tool_summaries.py`：删除 `scenario_summary`（其调用一并删除）。
- 提示词 `direct.md` / `analysis.md`：
  - 删「估值三档表 / 情景概率加权与风险收益比 / 三档目标价 / g\* 反推」引用
  - §3 改为：「本报告不输出任何目标价；估值结论以【估值安全边际表】为准，只做定性安全边际判断」
  - 保留「净现金调整 PE 必须引用」规则
  - 压力测试规则改为「直接引用【估值安全边际表】压力测试行，严禁重算」
  - 新增硬规则：**严禁输出任何目标价或三档价格**
- `_helpers._build_assertable_facts`：移除 `target_base`、`implied_growth`；`financial_verified`
  仅依赖 `fcf_dividend.dividend_paid`。

### 护栏影响
- `report_reconciler` 的「目标价 → target_base」映射：因 `target_base` 事实不再产生，变为 inert
  （报告出现「目标价」时无事实可比对，跳过重写）。保留关键词列表无副作用。
- 历史 g\* 符号算错护栏（`_valuation_consistency_flags`）随 g\* 删除一并移除——代价是失去该特定防错，
  但用户已明确要删 g\*；新增的「严禁输出目标价」硬规则 + 后端确定性压力测试从根上避免目标价类 BUG。

## 测试策略
- 删除：`test_tools_valuation*.py`、`test_scenario_prob*.py`、`test_table_render_valuation*.py`、
  `render_scenario_prob` 相关、`skeleton` 估值目标相关断言。
- 新增 `tests/test_margin_of_safety.py`：
  - `run()` 正常输出 pe/pb/net_cash_pe/peer_pe_median/stress 数值正确
  - 缺失 monetary_funds → net_cash_pe=None
  - 缺失 total_shares → stress eps=None → price=None
  - `render_margin_of_safety()` 输出含「估值安全边际」标题 + 压力测试两档下行空间
  - 全 None 输入 → 返回空串（与现有 render 约定一致）

## 验证
- `uv run --no-sync ruff check src/ tests/`（改动文件干净）
- `uv run --no-sync mypy src/aimoon/`
- `uv run --no-sync pytest -m "not integration" -q` 全绿
