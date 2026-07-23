# aimoon 全面性能优化设计（不降报告质量）

- 日期：2026-07-23
- 目标：在不降低报告数据准确性、格式完整性、内容完整度前提下，全面优化 aimoon 的性能。
- 首要指标：全面均衡（单次端到端耗时 + LLM 成本 + 批量吞吐 + 内存安全，按性价比排序落地）。
- 基准口径：含 1 次真实 AI 跑（冷基线，清 cache 前后各一次）。
- 基准股：000651（格力电器）。

## 现状热点（已剖析）

| 区域 | 问题 | 是否影响报告数据 |
|---|---|---|
| 启动 | `pipeline.py:23` 顶层 import `AkshareFinancialAdapter` → 触发 `akshare_adapter.py:16-17` 模块级 `import akshare as ak`，任何启动路径白等 1–3s | 否 |
| 采集 | `capital_flow.py:256-268` 拉全市场近 30 天龙虎榜再本地过滤，单次体量极大 | 否（数据结果一致） |
| 采集 | `peer_compare.py:231-279` AI 阶段为每只策展同业 `new QuoteCollector()` + 新 httpx，8–16 次请求 | 否 |
| 采集 | `akshare_adapter.py:129-171` 财务年报缓存命中后仍补拉分业务/巨潮 PDF/新浪兜底 | 否（命中仍联网） |
| 内存 | `akshare_adapter.py:66` `_raw_statements` 无界 dict，批量多标的持有全量 DataFrame 永不释放 | 否 |
| 渲染 I/O | `generator.py:135-146` 每次生成报告都拷 3 个 ~770KB vendor JS，多次运行累积 | 否 |
| 连接 | capital_flow 多源（pysnowball/akshare/东财 HTTP）各自新建客户端 | 否 |
| AI 阶段 | CLI 默认已是 DIRECT 单调用（1 次 LLM），thinking 默认关；结构性耗时在 gather_tool_context / 缓存命中 | — |

说明：报告本身只渲染一次、JS 已全本地化（离线可用），无模板内 DB/网络调用、无重复渲染——这部分无需改动。

## 优化项（按性价比排序，均不改变报告数据/格式）

### A. 启动加速 — akshare 懒加载
- 改动：`pipeline.py` 不再在模块顶层 import `AkshareFinancialAdapter`；改为在 `run()` 内首次真正构造财务适配器时再 import。
- 收益：`--help` / `--test` / `--mock` / 导入 提速 1–3s；真实采集路径无感（首次 collect 时触发）。
- 风险：低。回归保护：`--test`、`--mock`、真实采集均覆盖。

### B. 数据采集去浪费
- **B1. 龙虎榜采集 → 已取消**（`capital_flow.py`）
  - akshare 没有「按个股返回同形状汇总」的可靠接口：`stock_lhb_stock_detail_em(symbol,date,flag)` 席位明细在本版 akshare 恒抛 `TypeError` 且不含 `上榜原因`，无法重建报告字段；`stock_lhb_stock_statistic_em` 参数是时间周期非股票代码；`stock_lhb_ggtj_sina` 新浪源已失效。先落了 per-symbol 闸门（近 30 天未上榜跳过全市场拉取），后按用户要求**直接取消龙虎榜获取**：彻底移除 `CapitalFlowCollector._fetch_lhb` 及全市场/per-symbol 拉取分支、`_LHB_CACHE`、相关 import（`datetime/timedelta/DiskTtlCache`）。`lhb_date/lhb_reason/lhb_net_buy` 实体字段保留为空值，`prompt_builder`/`integrity_checker`/`index.html` 对空值均优雅降级，报告不再出现龙虎榜段落（000651 本就为空，内容不变）。
- **B2. peer_compare 复用共享客户端**（`peer_compare.py:231-279`）
  - 把 pipeline 共享的 `httpx.Client` 注入 peer 工具；命中 quote 磁盘缓存（60s），不再为每只同业 new `QuoteCollector()` + 新建连接池。
  - 省 8–16 次连接建立 / 重复请求，且复用已有 quote 缓存。
- **B3. 财务缓存命中仍联网**（`akshare_adapter.py:129-171`）
  - 把分业务（`stock_zygc_em`）、年报 PDF 附注（巨潮）、新浪三表兜底的结果一并纳入年报缓存键（同一 payload）。
  - 真命中 = 零网络；冷缓存仍正常拉取并写入完整 payload。数据不变。

### C. 内存边界
- **C1. `_raw_statements` 加 LRU + TTL**（`akshare_adapter.py:66`）
  - `dict[str, asyncio.Future]` 改为有上限（maxsize≈64）的 LRU，并加 TTL。单标的运行无感；批量多标的不再无界增长、不再长期持有全量 DataFrame。
- **C2. vendor JS 拷贝幂等**（`generator.py:135-146`）
  - 输出目录 `vendor/` 已存在且文件内容一致（按大小/哈希）则跳过 `shutil.copyfile`，省 ~770KB/次 I/O，多次运行不累积。

### D. 连接复用
- capital_flow 多源（pysnowball / akshare / 东财 HTTP）共用 pipeline 的 `httpx.Client`（通过依赖注入或模块级共享），减少重复构造客户端。

### E. 渲染（低风险，收益小）
- 仅含 C2 的 vendor 幂等化；Jinja2 已自动编译缓存，不做额外改动。

## 测量方案（含 1 次真实 AI 跑）

基准股：000651（格力电器）。

1. **冷基线（改动前）**
   - 清 `cache/`。
   - 启动：`python -c "import aimoon"` 计时。
   - 纯采集：`aimoon 000651 --test` 记录采集耗时（common/timing 输出）。
   - 全量：`aimoon 000651` 含真实 AI（DIRECT 默认），记录 wall-clock，保存 `output/report_before.html`。
2. **冷基线（改动后）**
   - 清 `cache/`。
   - 同样三步：启动计时、`--test` 采集耗时、全量 wall-clock → `output/report_after.html`。
3. **AI 结构性耗时**：由 `common/timing.py` 的 `logphase` 输出工具上下文构建、缓存命中等阶段时间。
4. **质量对账**：对比 `report_before.html` / `report_after.html` 关键数字（PE/PB、营收、净利、FCF、各维评分、龙虎榜/同业数据），确认一致；`aimoon --mock` 确认渲染链路无回归。

## 验收门槛（沿用项目约定）

- `uv run --no-sync ruff check src/` 与 `uv run --no-sync mypy src/aimoon/` 通过。
- `uv run --no-sync pytest -m "not integration"` 全绿。
- `aimoon 000651 --mock` 正常生成报告。
- 前后报告关键数据差异为 0（或仅格式级）。
- 产出本文档 + `docs/plans/2026-07-23-performance-optimization-report.md`（前后对比表）。

## 风险与回退

- B1/B3 缓存键变更：旧缓存条目失效但无害，清 `cache/` 即可。
- A 懒加载：确保所有真实采集路径首次调用时已触发 import，靠 `--test`/`--mock` 回归。
- **不改 report 模板结构、不改 AI prompt**，内容/格式严格不变。
- 若真实 AI 跑因 key/网络失败，回退为离线基准 + 说明。
