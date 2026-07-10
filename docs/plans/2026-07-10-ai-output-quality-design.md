# AI 分析输出质量深度优化 — 设计文档

- 日期：2026-07-10
- 状态：已与用户确认（brainstorming 第 1–5 问全部拍板）
- 范围：默认 **DIRECT 直出流** 的输出质量升级；不改采集层、不改已验证的 DIRECT 直出本身

## 背景与动机

过去二十轮已把 AI pipeline 重构为 DIRECT 直出（一次 LLM 出完整 8 节报告）+ 思考模式 + DDM 估值修复，并修了大量 bug。但 DIRECT 流当前**没有任何程序化质量护栏**：

- 数字真实性只靠 `direct.md` 软约束（"数字须来自文末系统表，否则写数据缺失"），无硬拦截；
- 之前写的 `skeleton_validator`（Kelly 公式/概率乘积/必填校验）只在 `--two-phase` 骨架流跑，DIRECT 路径不经过；
- DIRECT 流无领域知识注入、无联网检索（直出的 `_stream_llm_content` 不传 tools），深度受限于通用知识；
- 结论无结构化引用，用户无法核对依据。

用户确认要系统性升级四类质量：**抗幻觉/数字真实性、数字一致性、A 股深度/专业度、结论可追溯**，且采用：
- 校验机制 = **0-LLM 硬对账 + 轻量 LLM 自检** 双保险；
- 深度补法 = **静态领域知识包 + 可选实时检索** 双结合；
- 问题处置 = **对账→LLM 定点重写**（只改错句，不重写全文；仍存疑→页脚标注）。

## 总体架构

在 DIRECT 直出之后插入「质量护栏层」，前置「深度层」，收尾「可追溯层」：

```
_gather_tool_context()
   → _phase_direct()                         # 直出完整报告（不动）
   → [新增] _verify_and_fix()
        ├─ _reconcile_numbers()             # 0-LLM 硬对账
        ├─ if mismatches: _self_check_rewrite()   # LLM 定点重写（仅疑点非空时调）
        └─ _attach_credibility()            # 数据可信度页脚
```

原则：**护栏失败永不阻断报告**。对账器 / 自检任何异常都降级为"跳过校验"，报告照常生成，只在页脚标注"自检未执行"。质量升级不以稳定性为代价。

## 第 1 层：深度层（DIRECT 之前）

### 静态领域知识包 `prompts/domain_knowledge.md`
注入 DIRECT 系统提示（位于消息最前，享受既有前缀缓存，零额外成本）。内容聚焦 A 股特有、易错、立竿见影的点：
- 涨跌停规则：主板 ±10% / 双创(创业板·科创板) ±20% / ST ±5%；一字板；龙虎榜上榜门槛（日换手/涨跌幅/振幅）。
- 北向资金自 **2024-08** 起已停披——明确禁止 AI 编造"北向净流入/净流出"。
- 估值锚：PE/PB band 看**行业中位数**而非绝对值；股息率；分红除权对价的摊薄。
- 常见幻觉陷阱清单：总市值 ≠ 流通市值；季报 ≠ 年报；同比 ≠ 环比；成本价 ≠ 现价；把"机构持仓"当"北向"。

### 可选实时检索（默认关）
- 新增开关 `direct_web_search_enabled=False`。
- DIRECT 前用既有 web 检索能力拉该股近 3–5 条催化（公告/新闻/研报摘要），作为"近期催化"上下文块注入 user message。
- 默认关闭以控成本、控延迟；用户后续想开再开。

## 第 2 层：护栏层（DIRECT 之后）

### 0-LLM 数字对账 `ai/pipeline/report_reconciler.py`（新文件，纯 Python）
1. 把系统表（`tool_ctx` 内行情/财务/估值三档/资金流/Peer PE/K线统计）规范成"可断言事实"字典：
   `{pe_ttm, price, target_base, target_bull, target_bear, roe, rev_yoy, profit_yoy, div_yield, peer_pe_median, ...}`。
2. 用正则 + 单位/语境启发式从报告正文抽取 `(数值, 指标)` 声明。
3. 逐条对账，分级：
   - **严重**：表内根本没有该指标却被断言（虚构指标）。
   - **中**：数值超容差（如 ±5%）或单位混淆（亿/万）。
   - **中**：跨节矛盾（同一报告两节给不同目标价 / 不同 ROE）。
4. 产出 `mismatches: list[{snippet, claimed, expected, metric, severity}]`，供重写阶段与页脚复用。

### LLM 定点重写 `_self_check_rewrite(report_md, mismatches, tool_ctx)`
- 仅当 `mismatches` 非空才调用（成本条件化）。
- 提示词定位"严格事实核查员"，只给：报告全文 + 疑点清单 + 相关表片段。
- 要求对每条疑点判"真错 / 可接受"；真错则**只输出改正后的那句话**（不改全文），`thinking=False` 省思考 token。
- 用字符串替换把错句换掉；替换失败则保留原文 + 页脚标注"第 N 处未能自动修正"。

## 第 3 层：可追溯 + 报告集成

### 内联引用纪律（`direct.md` 收紧）
关键数字结论必须内联引用来源，如"ROE 25.1%（见基本面表）"；允许一套固定引用 token（行情表 / 基本面表 / 估值表 / 资金流表 / Peer 表 / K线表）。

### 数据可信度页脚
报告末尾新增"数据可信度"小节：
- 核对事实数、自动修正数、仍存疑条目清单；
- 复用已有的 `data_warnings`（行情/K线/资金流失败告警）。
用户一眼看清哪些数字被程序核验过。

## 文件改动清单

新增：
- `prompts/domain_knowledge.md` — A 股领域知识包
- `ai/pipeline/report_reconciler.py` — 0-LLM 数字对账（纯 Python，全 try/except 包裹）

修改：
- `orchestrator.py` — 接 `_verify_and_fix()`；DIRECT 前可选检索；`_attach_credibility()` 收尾
- `prompts/direct.md` — 注入知识包引用 + 引用纪律
- `analyzer.py` — DIRECT 可选接收检索上下文块
- `settings.py` — 新增 3 开关
- 报告模板 `report/templates/index.html` + `style.css` — 可信度页脚渲染

## 配置新增（`settings.py` / `.env`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `direct_web_search_enabled` | `False` | DIRECT 前实时检索催化（控成本默认关） |
| `reconcile_enabled` | `True` | 0-LLM 数字对账总开关 |
| `self_check_rewrite_enabled` | `True` | 疑点非空时 LLM 定点重写开关 |

DIRECT 仍保持 `thinking=True` + `effort=max`；自检阶段 `thinking=False`（纯修正，非推理）。

## 测试策略

- `tests/test_report_reconciler.py`：虚构 PE 被抓（严重）/ 数值超容差被抓（中）/ 亿万单位混淆被抓 / 清白报告 → 0 疑点。
- `tests/test_self_check_rewrite.py`：给定一条 mismatch，mock LLM 返回改正句，断言报告对应句被替换；替换失败断言页脚标注。
- 集成测试：mock DIRECT 输出含一个已知假数字 → 对账抓到 → 自检改正 → 最终报告数字与系统表一致。
- 护栏全程 `try/except`：对账/自检崩溃 → 跳过校验，报告不中断，页脚标注"自检未执行"。

## 预期收益

- 抗幻觉：虚构数字/指标被 0-LLM 硬拦截 + LLM 定点修正，DIRECT 流首次具备程序化护栏。
- 一致性：跨节矛盾、单位混淆被对账捕获。
- 深度：A 股领域知识包让分析不跑偏；可选实时检索补时效。
- 可追溯：内联引用 + 可信度页脚，用户可核对每份报告的核验覆盖。
- 成本可控：对账 0-LLM；自检仅疑点非空时触发且关思考；检索默认关。
