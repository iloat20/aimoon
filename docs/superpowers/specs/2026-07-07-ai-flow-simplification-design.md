# 子项目3: AI 流程简化 — 死代码清理 + 模式精简

日期: 2026-07-07
状态: 已批准

## 问题

orchestrator.py 有 ~80 行死代码;4 个运行模式逻辑高度重叠,参数和分支复杂。

## 方案

### A. 清理死代码

删除以下无调用方的函数/代码块:

| 位置 | 内容 | 行数 |
|------|------|------|
| `_stream_llm()` L452-465 | return 后不可达代码(旧 `_web_search_loop` 残留) | ~14 |
| `_llm_chat()` | 纯 wrapper,只被上述死代码调用 | ~4 |
| `_tool_results_to_messages()` | 模块级函数,无调用方 | ~8 |
| `_compact_tool_output()` | 只被 `_tool_results_to_messages` 调用 | ~55 |
| `_phase_output_text()` | 模块级函数,无调用方 | ~4 |

合计 ~85 行。

### B. 模式精简 4→2

**现状**:
- `default`: ANALYSIS(effort=high) + SELF_CHECK(effort=low) + COMPILE(effort=max)
- `fast`: ANALYSIS(effort=high), 跳 self-check + compile
- `single_call`: ANALYSIS(effort=max), 跳 compile
- `ultra_fast`: ANALYSIS(effort=max), 跳 compile

**目标**:
- `full`(默认): ANALYSIS(effort=high) + SELF_CHECK(effort=low) + COMPILE(effort=medium)
- `fast`: ANALYSIS(effort=high), 草稿直出

**改动**:
- `run()` / `_run_pipeline()` 签名: 删除 `use_single_call`、`use_ultra_fast` 参数,只保留 `use_fast: bool`
- 删除 `skip_self_check` / `skip_compile` 两个中间变量,统一用 `use_fast` 控制
- 删除 `_EFFORT_SINGLE` 常量(不再需要 max effort 的 ANALYSIS)
- CLI 侧 `--single-call` / `--ultra-fast` 参数删除(如果存在)

### 不改

- 工具两阶段 gather 结构(依赖关系决定,无法合并)
- `_run_safe` async 签名(asyncio.gather 需要 coroutine)
- 缓存逻辑(tool_cache / response cache)不变

## 影响

- 文件: `orchestrator.py`(主要)、`phases.py`(可能)、CLI `main.py`(如果暴露了相关参数)
- 测试: `test_pipeline_phases.py` 需更新(模式参数变化)
- 风险: CLI 参数删除属于 breaking change,但项目无外部用户

## 验证

- `ruff check src/` 通过
- `pytest tests/ -k "not integration"` 全部通过
- 确认 `_compact_tool_output` / `_tool_results_to_messages` / `_llm_chat` / `_phase_output_text` 无残留引用
