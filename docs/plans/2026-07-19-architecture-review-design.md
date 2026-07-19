# aimoon 架构全面审查与优化设计（2026-07-19）

**日期**: 2026-07-19
**状态**: 已确认，实施中（文档 + 立即重构）
**范围**: 全量架构审查（目录结构 / 模块划分 / 数据流向 / 接口设计 / 配置管理）+ #1–#8 优化落地
**关联**: `docs/plans/2026-07-09-architecture-refactor-design.md`（前次"六边形精练"路线，部分已落地）、`AGENTS.md`、`ARCHITECTURE.md`

---

## 0. 摘要

本次对 `src/aimoon/`（core 28 文件 + adapters 79 文件，共 107 个 .py）做了基于**实证**的全面审查（逐文件 grep 依赖方向、import 计数、样板比对、测试耦合扫描）。

**核心结论：六边形"依赖向内"的硬规则严格成立，这是最难的部分且当前是干净的，不要动。** 全部真实问题集中在 `driven` 层内部的**横切耦合、样板重复、上帝模块、配置膨胀、测试对私有符号的紧耦合**。

前次（2026-07-09）重构已落地了 `core/application/container.py` 泛型容器、`pipeline/` 子包拆分（`orchestrator.py` 865→766 行 + `_helpers.py`），以及部分 DRY 抽取。本设计在其基础上**补齐未竟项**并解决新暴露的横切耦合。

---

## 1. 健康项（明确不动）

| 项 | 证据 | 说明 |
|----|------|------|
| core 零反向依赖 | `core/` 全量 grep `import adapters` → 0 匹配 | 依赖规则严格成立 |
| DI 接线外置 | `core/application/container.py` 纯泛型 `Container`；真实 new 在 `adapters/driving/cli/pipeline.py:17-27` | 组合根正确外置 |
| 无循环依赖 | 方向严格 `core ← driven/* ← driving/cli` | 分层方向正确 |
| core 纯逻辑 | `core/domain/*` 仅 pydantic + logging/math/datetime | 无文件 IO |

---

## 2. 问题清单（按严重度）

| # | 问题 | 证据 | 严重度 | 归类 |
|---|------|------|--------|------|
| 1 | **report 反向依赖 ai**：presentation 引 AI 工具常量 | `report/generator.py:24` `from ..ai.tools.fcf_dividend import CGB_10Y` | 中 | 横切耦合 |
| 2 | **业务逻辑错层**：股债性价比 ~50 行在 report 层 | `report/generator.py:148-199 _build_equity_bond_signal` | 中 | 分层异味 |
| 3 | **collectors 依赖具体 financial adapter** | `collectors/capital_flow.py:146 import AkshareFinancialAdapter` | 中 | 横切耦合 |
| 4 | **上帝模块** | `ai/pipeline/orchestrator.py` 766 行 / 34 import；`_helpers.py` 232 行 | 高(风险) | 可维护性 |
| 5 | **Collector 样板重复** | `_get_client()` 逐字相同（capital_flow:42-45 vs kline:55-58）；`DiskTtlCache`+settings 标志反复；多级回退+`all_failed` 哨兵重复；`silent_failure+except` 重复；`fetch(**kwargs)` 全声明却**无人消费** | 高(收益) | DRY |
| 6 | **v2/legacy 双路径并存** | `analyzer.py` 两套；legacy 仅关标志可达；L1 `analysis:*` 缓存 v2 写 legacy 读 = 事实死读路径（`analyzer.py:211-213`） | 低-中 | 死代码 |
| 7 | **配置膨胀** | `config/settings.py` ~30 项；`deepseek_*`/`longcat_*` 整组重复；弃用别名 `deepseek_reasoner_enabled`；`resolve_ai_provider` 与 `AIProviderConfig` 职责重叠 | 中 | 配置管理 |
| 8 | **测试紧耦合私有符号** | import `_verify_and_fix`/`_quote_cache`/`_run_ai_analysis`/`mock_stock_analysis` | 中 | 可维护性(重构阻力) |

---

## 3. 优化方案（前后对比）

### #5 Collector 抽象抽取（高收益、低风险）
- **前**：每个采集器复制 `_get_client()` 惰性 httpx 初始化、`DiskTtlCache` 初始化、`all_failed` 哨兵多级回退、`silent_failure+except` 包裹；`DataCollector.fetch(symbol, **kwargs)` 声明 `**kwargs` 但 5 个子类**无一消费**。
- **后**：`BaseDataCollector` 提供
  - `_get_client()` 统一惰性 `httpx.AsyncClient`（带 `default_user_agent`）；
  - `_cache` 统一 `DiskTtlCache` 初始化（命名空间 + TTL 来自 settings）；
  - `@fallback` 装饰器 / `run_fallbacks()` 辅助实现多级回退 + `all_failed` 哨兵；
  - `silent()` 上下文管理器替代散落的 `try/except Exception: logging.warning`。
  - 子类只实现 `_fetch_impl()`，移除 `**kwargs` 样板。
- **收益**：新增采集器从 ~80 行样板降到只写 fetch 逻辑；样板集中可单测。

### #1 + #2 解耦 report 层（中风险、分层正确性）
- **前**：`report/generator.py` 从 `ai.tools.fcf_dividend` 引 `CGB_10Y`，股债性价比业务逻辑（~50 行）落在表现层。
- **后**：
  - 常量 `CGB_10Y` 与股债性价比计算搬入 **`core/domain/`**（新增 `core/domain/services/valuation_signals.py` 或 `core/domain/value_objects/` 纯函数模块），作为领域服务/值对象。
  - `report/generator.py` 只依赖 `core`，调用领域服务得到信号字典后渲染；移除 `from ..ai.tools...`。
- **收益**：消除 presentation→ai 的反向依赖，业务逻辑回归领域层，符合六边形。

### #3 解耦 collectors→financial（中风险）
- **前**：`collectors/capital_flow.py:146` 直接 `from ..financial.akshare_adapter import AkshareFinancialAdapter`。
- **后**：所需财务数据经 `StockAnalysis` 聚合 / 已采集的 `FinancialData` 暴露，或经 `FinancialDataCollector` 端口在 orchestrator 层组装后注入；collector 不再 import 具体 adapter 类。
- **收益**：collectors 与 financial 解耦，二者仅通过 core 端口/聚合通信。

### #4 拆分 orchestrator 上帝模块（高风险、动主链路）
- **前**：`ai/pipeline/orchestrator.py` 766 行 / 34 import，包揽上下文渲染、阶段调度、缓存、对账、流式输出。
- **后**：在已有 `pipeline/` 子包（已含 17 文件 + `_helpers.py`）基础上补全拆分：
  - `pipeline/context.py` —— 工具上下文拼装（现有 `context_renderer` 归并）；
  - `pipeline/phases.py` —— 阶段运行器（ANALYSIS/SELF_CHECK/COMPILE）；
  - `pipeline/cache.py` —— 骨架/终稿缓存读写（key 规范 `skeleton:`/`analysis:`）；
  - `pipeline/reconcile.py` —— `report_reconciler` 调用归并；
  - `orchestrator.py` 仅作**门面/编排**，≤200 行。
- **护栏**：每步保留外部行为；用 `aimoon <code> --mock` 与 `pytest -m "not integration"` 回归。

### #7 配置收敛（中风险、向后兼容）
- **前**：`Settings` 扁平 ~30 项；`deepseek_*` 与 `longcat_*` 整组重复（max_tokens/analysis_max_tokens/temperature）；弃用别名 `deepseek_reasoner_enabled`；`resolve_ai_provider()` 分支与 `AIProviderConfig` 职责重叠。
- **后**：
  - 嵌套 `DeepSeekConfig` / `LongCatConfig` 子模型（保持 `DEEPSEEK_*` / `LONGCAT_*` env 前缀兼容）；
  - `Settings` 暴露 `deepseek: DeepSeekConfig`、`longcat: LongCatConfig`；
  - 删除 `deepseek_reasoner_enabled` 别名（已在 MEMORY 标记弃用），校验集中到 `resolve_ai_provider()`；
  - `AIProviderConfig` 仅做数据载体，解析职责归 `resolve_ai_provider()`。
- **护栏**：`get_settings()` 单例与现有 `.env` 全量兼容，跑 `tests/test_settings.py`。

### #8 测试私有符号整改（中风险、重构阻力）
- **前**：`tests/` 扁平结构，直接 import 私有符号：`_verify_and_fix`、`_quote_cache`、`_run_ai_analysis`、`mock_stock_analysis`、`_helpers` 再导出。
- **后**：
  - 暴露**公共测试钩子**或经 core 端口测试行为（如 `StockAnalysisService.collect_and_analyze` 公共入口断言）；
  - 私有函数若需测试，提升为模块级公共函数（加 `_for_test` 或正式命名）并加契约注释；
  - `tests/` 按 `tests/core/`、`tests/adapters/driven/...` 镜像 `src/` 包深度（可选，先去私有耦合）。

### #6 legacy 路径清理（低风险）
- **前**：`analyzer.py` 同时有 v2(`_pipeline_analyze`) 与 legacy(`_legacy_analyze`)；L1 `analysis:*` 缓存 v2 写、legacy 读，legacy 非默认 → 读路径失效。
- **后**：
  - 保留 `use_pipeline_v2` 开关但明确 legacy 为 deprecated；
  - 统一缓存 key 规范（`skeleton:` vs `analysis:` 已区分），删除 v2 写/legacy 读的失效分支；
  - 在代码与 AGENTS.md 标注 legacy 弃用时间表。

---

## 4. 实施路线图（分批 + 验证）

验证四件套（每批后）：`uv run --no-sync ruff check src/` + `uv run --no-sync mypy src/aimoon/` + `uv run --no-sync pytest -m "not integration"` + `uv run --no-sync aimoon 600519 --mock`。

| 批次 | 内容 | 风险 | 验证重点 |
|------|------|------|----------|
| A | #5 Collector 抽象 + #1+#2 report 解耦 + #3 collectors→financial 解耦 + #7 配置收敛 | 低-中 | 全部测试绿、`--mock` 报告正常、`core` 仍零反向依赖 |
| B | #4 orchestrator 拆分 | 高 | 完整 pipeline 回归、流式输出不变、orchestrator ≤200 行 |
| C | #8 测试整改 + #6 legacy 清理 | 中 | 测试绿且不再 import 私有符号；legacy 标注弃用 |

> 注：本机已知隐患——持久化钩子会篡改文件（`tuple(`→`tuble(`），写文件后用 `grep -rc "tuble("` 验证；Windows safe-delete 钩子 fail-closed，磁盘缓存删除走 `_quiet_unlink()`。实施时优先用整文件 `Write` 重写，避免就地小改被钩子破坏。

---

## 5. 验收标准

1. `core/` 对 `adapters/` 零 import（保持）。
2. `report/` 与 `collectors/` 不再 import 任何具体 adapter 实现（只经 core 端口/聚合）。
3. 采集器新增/改造样板集中在 `BaseDataCollector`，子类无重复 `_get_client`/`DiskTtlCache`/回退。
4. `orchestrator.py` ≤200 行且为纯编排。
5. `Settings` 嵌套化，无弃用别名，`.env` 兼容。
6. 测试零私有符号 import。
7. 四件套全绿。

---

## 6. 风险与回滚

- **#4 orchestrator 拆分**风险最高：以"先抽子模块、门面保留原调用签名"方式渐进，每抽一个模块即跑回归；若行为漂移立即 `git stash` 回滚该批次。
- **#3 解耦**若 financial 数据获取路径复杂，优先"经聚合暴露"而非"端口注入"，降低改动面。
- 每批次独立 commit，便于逐批回滚。
