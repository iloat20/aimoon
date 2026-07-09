# AI 分析流程重构设计 — 骨架扩写架构

> 日期：2026-07-10
> 状态：已验证（brainstorming 完成，待实施）
> 范围：`adapters/driven/ai/pipeline/` 核心重构

## 1. 背景与问题

### 当前架构（v2 pipeline）

三阶段流程：ANALYSIS（4 节初稿）→ SELF_CHECK（LLM 伪修复）→ COMPILE（7-8 节长文）。

### 审查发现的 6 个问题

1. **ANALYSIS + COMPILE 内容高度重复**：COMPILE prompt 明确要求「不要沿用草稿，只基于原始数据生成」，实际是重新生成而非扩写。2 次完整深度 LLM 调用，成本翻倍。
2. **SELF_CHECK 章节校验 bug**：self_check.md 检查「8 章节」，但 analysis.md 只要求「4 节」，名称和数量都对不上，章节完整性检查永远失败。
3. **SELF_CHECK 伪修复**：返回 `fixes_needed` 但无修复 LLM 调用，只塞进 COMPILE prompt。orchestrator 注释写「1 次修复循环」但代码不存在。
4. **工具批次串行浪费**：`fcf_dividend` 只依赖 `fin`，却被困在批3 等 `valuation`。
5. **降级双重成本**：v2 失败 → 降级到 legacy（又一次完整 LLM 调用）。
6. **COMPILE prompt 自相矛盾**：注释说「只格式化/扩写」，prompt 说「不沿用草稿重新生成」。

## 2. 重构方案：骨架 + 扩写（职责分离）

### 核心思路

问题的根源是 ANALYSIS 和 COMPILE 职责没分开。ANALYSIS 已经写了完整文章，COMPILE 就变成重写。

**让两者真正分工**：
- ANALYSIS → 输出**结构化 JSON 骨架**（推理结论 + 数字 + 逻辑链），不写完整文章。深度推理在这里完成。
- COMPILE → 纯扩写，把骨架渲染为完整长文。不做任何推理，只做写作。

### 新架构三阶段

```
Phase 1: ANALYSIS → JSON 骨架
  - 工具并行（2 批，依赖触发式调度）
  - LLM 输出 JSON 骨架（```json 代码块）
  - reasoning_effort=high, max_tokens=4096
  - 骨架承载所有推理结论

Phase 1.5: SELF_CHECK → 程序化校验
  - 0 LLM 调用
  - Python 代码校验：格式/数学一致性/数字比对/必填
  - 秒级完成

Phase 2: COMPILE → 纯扩写
  - 基于骨架 JSON 扩写完整长文
  - reasoning_effort=medium
  - prompt：「基于骨架扩写，禁止重新推理或编造骨架外数字」
```

### 降级策略（0 条调 legacy）

| 故障点 | 新降级 | LLM 成本 |
|---|---|---|
| ANALYSIS 超时/空 | 工具表格 + 数据汇总模板 | 0 |
| 骨架 JSON 解析失败 | 容错提取部分字段，失败则同上 | 0 |
| SELF_CHECK 校验失败 | fixes 注入 COMPILE（校验本身 0 LLM） | 0 |
| COMPILE 超时/空 | 骨架 + 表格渲染为 Markdown | 0 |

### 预期收益

- token 减 ~45%（骨架 800-1200 tok vs 旧初稿 2500-3500 tok；COMPILE 输入也减少）
- 耗时减 ~40%（砍 SELF_CHECK 60s + COMPILE 不再重推理）
- 降级路径从「再调一次 LLM」变为「0 LLM 模板渲染」

## 3. 骨架 JSON Schema

```json
{
  "data_audit": {
    "missing": [
      {"field": "分红支付率", "importance": "high", "estimable": true}
    ]
  },
  "data_inference": [
    {
      "field": "分红支付率",
      "formula": "FCF/净利润",
      "base": 0.45, "optimistic": 0.55, "pessimistic": 0.30,
      "price_impact": "若30%而非50%,目标价下调12%"
    }
  ],
  "narratives": {
    "macro":    {"probability": 0.60, "consensus": "...", "our_view": "...", "falsify": "10年国债>3.5%→-8%"},
    "industry": {"probability": 0.70, "consensus": "...", "our_view": "...", "falsify": "价格战持续>6月→-12%"},
    "alpha":    {"probability": 0.65, "consensus": "...", "our_view": "...", "falsify": "管理层变动→-15%"}
  },
  "composite_prob": 0.27,
  "forensic_audit": {
    "items": [
      {"item": "OCF/利润背离", "status": "正常", "detail": "1.2倍,3年稳定"},
      {"item": "应收vs营收", "status": "关注", "detail": "应收增速15%>营收增速8%"}
    ],
    "dupont": {"net_margin": 0.52, "turnover": 0.45, "leverage": 1.8},
    "quality_score": 8,
    "red_flags": ["应收增速超营收"]
  },
  "valuation": {
    "targets": {"conservative": 1500, "neutral": 1800, "optimistic": 2100},
    "implied_g": 0.04,
    "peer_pe": {"self": 30, "peer_avg": 25, "premium_pct": 20},
    "expectation_gap": "过度乐观",
    "sensitivity": [{"param": "折现率+1%", "impact": "-8%"}]
  },
  "kelly": {
    "b": 2.5, "p": 0.27, "q": 0.73, "f_star": 0.04,
    "position": 0.02, "rating": "增持"
  },
  "red_team": [
    {"bull": "品牌溢价可持续", "bear": "消费降级压缩高端需求"}
  ],
  "decision_tree": [
    {
      "event": "Q3财报", "trigger": "营收同比<-10%", "prob": 0.25,
      "data_node": "2025-10",
      "action_triggered": "减仓50%", "action_else": "持有",
      "price_impact": {"up": "+5%", "down": "-12%"}
    }
  ],
  "self_critique": {
    "bear_attacks": [
      {"assumption": "毛利率90%+", "attack": "竞品降价可能压缩"}
    ],
    "judge": "承认风险,品牌护城河短期难破;维持评级,目标价下调3%"
  },
  "stress_test": {
    "scenario": "营收同比降15%+净利率压缩5pct",
    "stress_fcf": 3000000000,
    "dividend_coverage": 1.8,
    "floor_price": 1200,
    "floor_downside_pct": -24,
    "verdict": "能维持但无余裕"
  }
}
```

### Schema 设计原则

- 所有数字为数值类型（非字符串）→ 可程序化校验
- 概率用 0-1 小数（非"60%"）→ 可数学验证 `composite_prob ≈ macro × industry × alpha`
- status 用枚举（正常/关注/危险）→ 可枚举校验
- 每个结论带 detail/evidence → COMPILE 扩写时有素材
- 骨架约 800-1200 token（vs 旧初稿 2500-3500 token）

## 4. SELF_CHECK 程序化校验逻辑

零 LLM 调用，纯 Python 代码校验：

1. **格式校验**：骨架 JSON 能否解析？必填顶层字段是否存在？
2. **数学一致性**：
   - `composite_prob ≈ narratives.macro.prob × industry.prob × alpha.prob`（±0.05 容差）
   - Kelly 公式：`f_star = (b*p - q) / b` 是否成立？（±0.01 容差）
   - `position ≈ f_star × 0.5`（半凯利，±0.01 容差）
3. **数字比对**：骨架中的估值数字（PE/目标价/ROE）与 `tables_md` 系统表格是否一致？
4. **必填完整性**：kelly.b/p/q、valuation.targets 三档、narratives 三层、forensic_audit.quality_score

校验失败时，不通过项作为 `fixes` 列表注入 COMPILE prompt（保留旧的"提示"机制，但 0 LLM 成本）。

## 5. 工具批次优化

### 当前（3 批串行）

```
批1（5并行）: technicals, financial_temporal, business_moat, peer_compare, sentiment
批2（2并行）: risk_quant(←fin), valuation(←fin,peer)
批3（2并行）: fcf_dividend(←fin), scenario_prob(←val)
```

### 优化后（2 批，依赖触发式）

```
批1（5并行）→ fin/peer 就绪
  ↓
批2 同时启动: risk(←fin) | valuation(←fin,peer) | fcf(←fin)
              ↓ valuation 完成
              立即启动 scenario(←val)  ← 不等 risk/fcf
```

使用 `asyncio.create_task` 实现：批2 中 risk/val/fcf 并行启动，val 完成后立即创建 scenario task，不等 risk 和 fcf。

总等待从 3 轮降到 2 轮。

## 6. 提示词重构

### analysis.md

- 角色不变（对冲基金逆向策略师 + 法证会计）
- 思维框架不变（三层叙事/法务会计/隐含预期/Kelly/反向论证/决策树/压力测试）
- **输出格式改为 JSON 骨架**（```json 代码块内）
- 末尾附 schema 示例 + 字段约束说明
- 明确：每个数字必须能在系统表格找到来源，否则写 null + detail 说明缺失

### compile.md

- 新增 `{{ skeleton }}` 占位符注入骨架 JSON
- 核心指令：「骨架是权威推理结论，你只负责把它变成读者友好的长文」
- 「每个数字必须在骨架中有对应，骨架没有的数字一律写"数据缺失"」
- 「禁止重新推理、禁止编造骨架外数字」
- 章节结构保持 7-8 节

### self_check.md

- **删除**（改为 Python `skeleton_validator.py` 程序化校验）

## 7. 文件改动清单

### 新增 3 个文件

| 文件 | 职责 |
|---|---|
| `pipeline/skeleton_schema.py` | 骨架 Pydantic model（字段定义 + 类型约束） |
| `pipeline/skeleton_validator.py` | 程序化校验（格式/数学一致性/数字比对/必填） |
| `pipeline/skeleton_renderer.py` | 骨架→Markdown 渲染（降级 + 快速模式共用） |

### 修改 7 个文件

| 文件 | 改动 |
|---|---|
| `pipeline/orchestrator.py` | 三阶段核心重写 + 工具 create_task 调度 + 降级路径改为骨架渲染 |
| `pipeline/prompts/analysis.md` | 输出格式改为 JSON 骨架 |
| `pipeline/prompts/compile.md` | 改为基于 `{{ skeleton }}` 扩写 |
| `pipeline/prompts/self_check.md` | 删除 |
| `pipeline/phases.py` | SELF_CHECK 的 PhaseSpec 标记 `needs_llm=False` |
| `pipeline/utils.py` | 新增 `parse_skeleton_json()` 容错解析 |
| `config/settings.py` | `analysis_max_tokens` 默认 8192→4096 |

## 8. 测试计划

### 单元测试

- `skeleton_validator`：合法骨架 / 缺字段 / 数字不一致 / Kelly 公式错误 / 概率乘积不一致
- `skeleton_renderer`：骨架→Markdown 渲染输出完整性
- `parse_skeleton_json`：纯 JSON / 带markdown包裹 / 带前后噪声文字 / 畸形 JSON

### 集成测试

- mock LLM 返回合法骨架 → 完整 pipeline 走通
- mock LLM 返回非法 JSON → 降级到骨架渲染
- mock ANALYSIS 超时 → 降级到数据汇总
- mock COMPILE 超时 → 降级到骨架渲染

### 回归测试

- 对比新旧架构报告质量（人工抽检 3-5 只标的）
- 对比 token 消耗 before/after

## 9. 向后兼容

- `_legacy_analyze` 保留不动（`use_pipeline_v2=False` 时的回退）
- CLI 参数不变（`--fast`/`--single-call`/`--ultra-fast` 行为适配新架构）
- 缓存 key 格式不变（`analysis:{symbol}:{date}`），缓存内容为 COMPILE 终稿
- 快速模式（`use_fast` 等）跳过 COMPILE 时，直接用骨架 + 表格渲染

## 10. 实施顺序建议

1. 新建 `skeleton_schema.py`（Pydantic model）
2. 新建 `skeleton_validator.py` + 单元测试
3. 新建 `skeleton_renderer.py` + 单元测试
4. 重写 `analysis.md`（JSON 骨架输出格式）
5. 重写 `compile.md`（基于骨架扩写）
6. 删除 `self_check.md`，更新 `phases.py`
7. `utils.py` 新增 `parse_skeleton_json()`
8. 重写 `orchestrator.py`（三阶段 + 工具调度 + 降级）
9. 更新 `settings.py`（max_tokens 默认值）
10. 集成测试 + 回归验证
