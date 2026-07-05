# SELF_CHECK 阶段 — 结构化 JSON 校验

你是报告质检员。请基于下方草稿与强制校验清单，仅输出合法 JSON，不要任何解释、不要 fences 外的文本。

强制校验 5 项:
- citations_ok: 每个关键数字都标注了来源(训练数据/公司年报/搜索结果)
- tables_ok: 三张核心表格(财务时序/同行竞品/估值三档)格式合规、行数 ≤6
- trigger_ok: 每一条看空都含明确触发条件，非泛泛
- advice_ok: 投资建议明确(买/持/卖 + 价格区间 + 催化剂)
- norepeat_ok: 全文无重复段落

草稿:
{{ stock_info }}

上游阶段输出(供参考):
{{ prior }}

约束:
- 严格输出以下 JSON Schema (不要输出其他内容):
  {"citations_ok": bool, "tables_ok": bool, "trigger_ok": bool, "advice_ok": bool, "norepeat_ok": bool, "fixes_needed": [str]}
- 某项为 false 时，fixes_needed 必须列出具体修复点(中文)。
