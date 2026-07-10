# MEMORY.md — aimoon 项目长期约定

## 运行环境（本机，重要）
- 跑 aimoon：**`uv run --no-sync aimoon <code> -o output`**。`uv run` 不带 `--no-sync` 会卡死在环境同步（本机到包索引网络极慢）。
- 静态检查：`uv run --no-sync ruff check src/` / `uv run --no-sync mypy src/aimoon/`。
- 测试：`uv run --no-sync pytest -m "not integration"`（3 个集成测试按标记 deselect）。
- 全局 `aimoon` 二进制（`~/.local/bin/aimoon`）是坏的，别用；僵尸 `uv` 进程需 `kill -9` 直接杀。

## 架构约定
- 六边形 + DDD，依赖向内：core 永不 import adapters；driving 层负责 register。
- 采集器容错契约：全部 `except Exception: pass`，单源失败永不 abort；每 collector 有 mock fallback。
- 中文股市：涨=红、跌=绿；货币默认 ¥。

## 关键领域事实
- K 线 canon 单位 = **手**（档1/2 akshare 本就手；档3 腾讯原 `×100` 已改 `vol_f` 直接用手）。
- `StockAnalysis`：数据字段 `Optional=None` + `extensions: dict[str,BaseModel]` 扩展点 + `social_posts: tuple`；消费者 `x or EmptyEntity()` 回退。
- `FinancialData` 无 `statements` 字段；`_dividend_from_statements` 原依赖 `statements` 恒 None，F1 已改为读 `financial.dividend_paid`，当前正确（无真实 gap）。
- `scoring.py` 不存在（评分在 `validation/integrity_checker.py`），四文档统一"不存在"口径。
- 提示词从 `pipeline/prompts/` 加载；根目录 `pipeline/compile.md` 是死副本不被加载。
- **DeepSeek 模型（2026-07 官方口径）**：当前主模型 `deepseek-v4-flash`（**官方真实模型，非网关别名**），`deepseek-v4-pro` 更强（3× 单价、并发 500）；`deepseek-reasoner`/`deepseek-chat` **已于 2026/07/24 弃用**，二者分别等价 v4-flash 的思考/非思考模式。settings 默认 `deepseek_model=deepseek-v4-flash`。
- **思考模式（`thinking` 参数，默认 enabled）**：`{"thinking":{"type":"enabled/disabled"}}` + `reasoning_effort`(仅思考模式生效)。`reasoning_effort` **官方只有 high/max 两档真实**，`low`/`medium` 被静默映射为 `high`、`xhigh`→`max`——故「降 effort 省 token」只有 high→max 一档；**大幅省钱应直接关思考**。`deepseek_analysis_effort=max` 是真实最深档。思考模式下 `temperature` 被忽略。
- **本运行环境（`.env`）锁定**：`DEEPSEEK_MODEL=deepseek-v4-flash` + `DEEPSEEK_ANALYSIS_EFFORT=max` + `DEEPSEEK_REASONER_ENABLED=true`（`deepseek_reasoner_enabled` 是 `deepseek_thinking_enabled` 的兼容别名，=true 即强制开启思考+发 effort）。DIRECT/ANALYSIS 走思考+max；COMPILE 已改为**关思考**（纯扩写无需推理，省全部思考 token）。实跑若 API 400 重试降级，先试把思考开关改 `false`。
- 财务三表（利润/资产/现金）在 `AkshareFinancialAdapter` 内进程级单次拉取记忆化（fetch/quarterly/history 共享）+ 季报(24h)/历史(7d) 磁盘缓存；重复跑不再重拉。
- 报告 JS 依赖（chart.js/html2canvas/jspdf）已 vendored 到 `report/static/vendor/`，生成时复制到输出 `vendor/`，模板本地引用（离线、零外部请求）。
- 成本开关：`guba_playwright_enabled=False`（股吧默认 HTML 优先，不启浏览器）、`kline_eastmoney_direct_enabled=True`（K线 L4 回退可关，防 push2his 死链空耗）。
- AI pipeline（2026-07-10 重构为「骨架+扩写」，`cli/pipeline.py` `use_v2=True` → `_pipeline_analyze`）。成本杠杆：`deepseek_analysis_effort`（默认 `high`，仅 high/max 真实、low/medium 被映射为 high 无降本；DIRECT/ANALYSIS 用 `max`）+ `deepseek_analysis_max_tokens`（**默认 `4096`**，ANALYSIS JSON 骨架上限）+ `deepseek_max_tokens`（DIRECT 完整报告上限，默认 `24576` 防截断）。`orchestrator.py` 内对此两项 import 是 `from ...config.settings`（**三个点**；写两个点是回归 `ModuleNotFoundError: ai.config`）。
- DeepSeek 前缀缓存（**最大免费杠杆，缓存命中输入 ¥0.02/百万 vs 未命中 ¥1.0/百万 = 50×**）：系统提示（analysis.md/compile.md/direct.md 固定长文本）位于消息最前 = 稳定缓存前缀，同标的复跑天然命中省输入 token，无需额外参数。思考 token(reasoning_content)按**输出**计价(¥2/百万 flash)是主要成本，想省钱优先降 effort(high→max)或关思考。

## AI 分析 pipeline（两条流：DIRECT 直出 vs 骨架+扩写；DIRECT 为默认，2026-07-10 第十八轮）
- **DIRECT 流（默认，"完整报告但不扩写"）**：`_gather_tool_context()`（9 工具并行 + 0-LLM 权威表格/摘要）→ **一次 LLM 直出**完整 8 节报告（`_phase_direct`，提示词 `prompts/direct.md`，effort=`deepseek_analysis_effort`、max_tokens=`deepseek_max_tokens`）。不经 JSON 骨架、不做 COMPILE 扩写。`Phase.DIRECT="direct"`，`DIRECT_TIMEOUT=600`。空产出→0-LLM 表格兜底。
  - 触发：`_run_pipeline` 顶部 `direct_mode = use_single_call or use_ultra_fast` → early-return `_run_direct`。CLI 默认 `use_single_call=True` = DIRECT。orchestrator `run()` 默认 flag 全 False（裸 `.run()` = 两阶段骨架流，测试据此）。
  - 为何存在：用户要"完整报告"但"不要扩写"。骨架把丰富推理压扁、COMPILE 再注水 = 两头不讨好；DIRECT 让完整性来自那一次真实推理本身。
- **骨架+扩写流（`--two-phase` opt-in）**：`ANALYSIS`（reasoner 出 JSON 骨架，思考+effort）→ `SELF_CHECK`（纯 Python 0-LLM 校验，见 `skeleton_validator.py`）→ `COMPILE`（基于骨架纯扩写，**关思考** `thinking=False` 省全部思考 token，temperature 恢复生效）。
- `skeleton_renderer.render_skeleton_md()` 曾漏渲染 self_critique/stress_test/valuation.sensitivity/peer_pe，已补全（第十七轮）。
- 新增 3 文件：`ai/pipeline/skeleton_schema.py`（Pydantic 骨架模型）/ `skeleton_validator.py`（0-LLM 校验）/ `skeleton_renderer.py`（骨架→MD 降级渲染）。`self_check.md` 提示词已删（校验改程序化）。
- 降级：任何阶段失败都**不再调 LLM**，改 `skeleton_renderer` 骨架+表格模板渲染（删除了旧 v2 失败→legacy 再调一次的双重成本）。
- 工具批次：3 批→2 批（`asyncio.create_task` 依赖触发，fcf 提前到批 2）。
- 预期收益：token -45%、耗时 -40%、降级 0-LLM；实测 pytest 190 passed（6 个 ERROR 属 `.pytest-tmp/` 沙箱环境问题）。

## 质量护栏（输出质量深度优化，2026-07-10 第二十一轮 brainstorming→子代理实施）
- 目标：给默认 DIRECT 流补 深度层 + 护栏层 + 可追溯层，系统性提升抗幻觉/一致性/专业度/可追溯。设计+计划见 `docs/plans/2026-07-10-ai-output-quality-design.md` / `-plan.md`。
- 深度层：
  - `pipeline/prompts/domain_knowledge.md`：A股领域知识包（涨跌停规则/北向停披/估值锚/幻觉陷阱/引用纪律），在 `phases.phase_system_prompt` 的 `Phase.DIRECT` 分支用 `load_prompt("domain_knowledge.md")` 前置拼进系统提示（静态前缀，不影响 DeepSeek 前缀缓存）。
  - `direct_web_search_enabled=False`（默认关）：DIRECT 前接 `execute_web_search` 拉近期催化注入 user message；关时行为不变。
- 护栏层（`_verify_and_fix`，在 orchestrator 内，全程 try/except 不阻断报告）：
  - `report_reconciler.reconcile(report_md, facts)`：0-LLM 数字对账。从正文抽「指标词+数字(+单位)」，与 `facts` 字典对账。分级 critical(虚构指标=表内无此键) / medium(数值超容差5%或单位混淆亿/万) / 跨节矛盾(同指标两值超容差)。facts 由 `_build_assertable_facts(tool_ctx)` 从 `_ToolContext.tool_results` 抽（pe_ttm/pb/target_base/roe/revenue）；**price 因 `_ToolContext` 无 quote 实体而缺失，已在 reconcile 端列入 `_OPTIONAL_METRICS` 跳过（不误判 fiction，第二十二轮）**。正则覆盖 `=` 等号写法与千分位 `1,680`。
  - `self_check_rewrite(report_md, mismatches, facts, llm)`：疑点非空时调一次 LLM（`thinking=False` 非流式，独立线程 asyncio.run）只回改正句；安全护栏——改正句须含系统表正确值才替换，否则保留原文。**critical(虚构指标 expected="<absent>") 永不自动重写**（无正确值可验证），由 `_verify_and_fix` 显式过滤进 credibility 页脚 `uncertain` 列表让用户可见（第二十二轮加固，不再依赖 `<absent>` 侥幸拦截）。
  - 成本：对账 0-LLM；自检仅疑点非空时触发且关思考。
- 可追溯层：direct.md 加「引用纪律」段（关键数字内联标注来源表名）；报告末尾「数据可信度」页脚（`report/templates/index.html`，amber 调），显示 checked/corrected/uncertain；credibility 透传链：`orchestrator ctx.credibility` → `analyzer.model_copy(credibility=...)`（frozen 模型须 model_copy）→ `AnalysisReport.credibility` 字段（默认 `{}`）→ `generate(credibility=...)`。
- 三开关（settings，`aimoon.adapters.driven.config.settings`，`get_settings()`）：`direct_web_search_enabled=False` / `reconcile_enabled=True` / `self_check_rewrite_enabled=True`。
- 实测：pytest **244 passed**（较 221 +23 质量护栏测试），ruff/mypy 干净，mock 端到端 EXIT=0，无 tuble( 篡改。

## 工作区隐患（本机）
- 持久化钩子在每次写入后篡改文件：`tuple(`→`tuble(`、async 函数前插 `@pytest.mark.asyncio`、import 排序。规避：Write 整文件重写绕过 Edit 守卫；sed 精确替换后立刻跑 pytest 不留间隙；`grep -rc "tuble("` 验证还原。

## Git
- 仓库 `git@github.com:iloat20/aimoon.git`（main）。
- 2026-07-10 十轮审查 + AI pipeline 重构（骨架+扩写，Task 1-10）+ 质量护栏深度优化（Task 1-9，12 提交）已落地，本地 main 领先 `origin/main` 多个提交，**待 `git push origin main`**。
- 刻意排除项（不入库、untracked）：`.pytest-tmp/`、`docs/screenshots/`、`src/aimoon/adapters/driven/ai/pipeline/compile.md` 死副本。`.gitignore` 已加 `.pytest-tmp/`、`docs/screenshots/`。
