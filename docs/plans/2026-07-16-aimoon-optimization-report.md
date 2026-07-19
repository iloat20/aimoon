# aimoon 深度审查与优化报告

> 生成日期：2026-07-16 · 审查范围：核心代码 / 配置 / 文档 / 报告产物 · 交付定位：诊断 + 落地修复方案
> 审查方法：4 路并行代码侦察（Pipeline·AI·报告·配置文档）+ 主审对 HIGH 级结论逐条亲验（带真实 file:line）

---

## 0. 执行摘要（TL;DR）

一句话结论：**你当前打开的那份 `600519_20260716_071313.html` 是 `--mock` 模式产出的，不是真实分析报告**，所以它"质量差"是必然的——AI 正文来自 `common/mock.py` 的静态 5 节占位文案，附录与可信度整块空白。这掩盖了两类真问题，本报告把它们一并挖了出来：

| 维度 | 关键发现 | 最高级别 |
|---|---|---|
| **报告质量** | mock 文案含"北向增持"幻觉且与数据矛盾；估值仪表盘 clamp bug 会把"价格超出区间"误标为"超跌 0%"；跨源取价；¥ 缺失 | 🔴 高 |
| **Pipeline 性能** | 磁盘缓存 get/set 同步阻塞事件循环；部分字段缓存命中后仍被无条件重复拉取；quote / pysnowball 串行在 gather 外 | 🔴 高 |
| **AI Token 成本** | DIRECT 默认流满配跑（`effort=max` + 思考默认开），思考 token 按输出计价=最大成本；v2 无条件写 `analysis:*` 缓存且会缓存降级结果 | 🔴 高 |
| **配置/文档** | README/AGENTS/CLAUDE 多处描述已与代码脱节（财务源写 pysnowball 实为 akshare；引用不存在的 `data_cleaner.py` / `scoring.py`） | 🔴 高 |

预期整体收益：真实分析 **token 成本可降 40–70%**（关默认思考 + 去缓存污染 + 前缀去重），**端到端墙钟时间可降 20–35%**（缓存异步化 + 消除重复拉取 + 并行度提升），报告可读性与可信度显著提升（修复仪表盘/幻觉/¥/附录空白）。

---

## 1. 报告质量（Report Quality）

### 🔴 R1 · 用户当前报告是 mock 产物，且 mock 文案质量低、含幻觉
- **现象**：`output/600519_20260716_071313.html:407` 明示"来源: Mock数据"，现价 48.56（茅台真实价 ~1400+），AI 正文仅 5 节（`:905-915`），附录/可信度空白。
- **根因**：`src/aimoon/adapters/driven/common/mock.py:36-52` 硬编码静态 `report_text`，其中 `:44` "近期主力资金呈净流入状态，北向资金小幅增持"——**北向已停披，属确定性幻觉**，且与资金面卡片（无北向行）矛盾。
- **影响**：mock 用于演示/测试，但这份文案会误导阅读者，也让"报告质量差"的表象掩盖真实链路是否健康。
- **方案**：① 重写 mock 文案，去掉一切方向性断言（尤其北向），改为明确标注"示例占位、非真实分析"；② mock 现价改为贴近量级的合理值或直接标注"示例价"；③ mock 模式下也填充 `data_appendix_md`/`credibility`（用 mock 数据渲染确定性表），避免"空报告"观感。
- **预期效果**：mock 报告不再误导，且能真实反映模板/附录渲染链路是否正常。

### 🔴 R2 · 估值百分位仪表盘 clamp bug（价格超区间被误标"超跌/极高"）
- **根因**：`src/aimoon/adapters/driven/report/templates/index.html:182-184`——`pct = (cur-lo)/(hi-lo)`，当 `cur<lo` 得负值被 `:183` clamp 到 0 → 落入 `:187 pct<20` → 标签"超跌"；`cur>hi` 同理 clamp 100 → "极高"。模型**无法区分"真超跌"与"价格根本不在 K 线区间（数据不一致）"**。
- **附带**：`:181` 用 `quote.price`（行情源）对 `:178` K 线 `close`（K线源）比较，**跨源**；两源不一致时（如 mock）必然错判。
- **影响**：仪表盘给出误导性技术位判断，是报告可信度硬伤。
- **方案**：模板增加 `price ∉ [lo,hi]` 分支，显式渲染"现价超出近 N 日区间（数据待核）"而非套用超跌/极高；并在生成侧尽量保证 quote 与 kline 同源或加一致性校验。
- **预期效果**：消除误判，异常数据被显式暴露而非伪装成结论。

### 🟡 R3 · 附录死锚点 + 未使用的告警条 CSS
- **根因**：ToC"数据底稿"锚点指向 `#card-appendix`，但 `data_appendix_md` 为空时该 card 不渲染（`index.html:349 {% if data_appendix_md %}`）→ 点击跳空；`.data-warning-bar`（`index.html:332-340`）为定义但从不使用的死 CSS。
- **方案**：`data_appendix_md` 为空时用 `.data-warning-bar` 渲染"底稿未生成"占位（复活死 CSS + 消除死锚点）。

### 🟡 R4 · 货币 ¥ 符号缺失
- **根因**：`direct.md` 要求"货币¥"，但正文/ mock 金额均为裸数字（"141.99 亿"）。
- **方案**：关键金额统一前缀 ¥ 或"人民币"；可在 table_renderer 层集中处理。

### 🟢 R5 · direct.md 规则本身高质量，问题在链路
- direct.md 的 few-shot、"数据缺失≤1 处""禁止 1.7e11 灾难 token""Kelly 不展示"等规则质量高。当前 mock 报告完全不体现，**根因是 mock 链路不走 direct.md**，规则无需改，需保证真实链路正确应用 + 上游 `data_appendix_md`/`credibility` 透传。

---

## 2. Pipeline 性能与正确性

### 🔴 P1 · 磁盘缓存 get/set 同步阻塞事件循环
- **根因**：`src/aimoon/adapters/driven/common/cache.py:62`(get)/`:79`(set) 在 async 协程内直接 `read_text/write_text` + `json.loads`。调用点密集且都在 `await` 路径：`quote.py:51/57`、`kline.py:68/74`、`financial/akshare_adapter.py:122/145/161/316/802/832/907/917`。
- **影响**：并行采集时，任一缓存 IO 都会卡住整个事件循环，抵消 `asyncio.gather` 的并行收益。
- **方案**：`DiskTtlCache.get/set` 用 `loop.run_in_executor`/`asyncio.to_thread` 卸载到线程池（或提供 async 版本）。
- **预期效果**：并行采集墙钟时间下降；缓存命中场景收益尤其明显。

### 🔴 P2 · 部分字段缓存命中后仍被无条件重复拉取
- **根因**：`financial/akshare_adapter.py`——`segment_revenue` 在 `:131` 补拉 `stock_zygc_em`，随后 `:291` **无条件再拉一次**；`annual_report_footnotes` 同理（`:142` 与 `:299`）。
- **影响**：每次真实分析多打 2 次可避免的重型 F10/PDF 请求，拖慢且增东财 WAF 命中风险。
- **方案**：`:291`/`:299` 复用 `:131`/`:142` 结果，或缓存命中分支直接跳过无条件重拉。

### 🟡 P3 · quote 与 pysnowball 主源串行在 gather 之外
- **根因**：`ai/…/orchestrator.py:120` quote 在并行块前单独 `await`；`capital_flow.py:54` pysnowball 主源先 `await` 再 `:58` gather 其余三源。二者与后续源无数据依赖。
- **方案**：将 quote / pysnowball 并入同一个 `asyncio.gather`（quote 失败时 social 用 `name` 兜底）。
- **预期效果**：端到端延迟减少≈这两步的串行耗时。

### 🟡 P4 · 宽 except 吞掉编程错误
- **根因**：`common/retry.py:23 silent_failure` 捕获所有 `Exception` 仅 warning/debug；`capital_flow` 的 `_fetch_northbound/_fetch_lhb` 整段被包，真实 bug（AttributeError 等）被静默。
- **方案**：对编程错误类异常（AttributeError/TypeError/KeyError）不吞，仅吞网络/解析类。

### 🟢 P5 · 次要项
- `cache.py:65-67` 先 `exists()` 后 `read_text()` 存在 TOCTOU（已被 `_quiet_unlink` 容错，风险低）。
- `retry.py:56 async_retry_on_connection` 已实现但无调用方（死代码）。
- `composite_repo.py:56-58 get_collect_results` 依赖 `collect_all` 先跑，状态时序耦合。
- ✅ 已确认无隐患：`cache.py` 无裸 unlink（全走 `_quiet_unlink`）；财务三表经 Future 记忆化并行共享，无重复拉取；重型 IO 均已 `to_thread` 卸载。

---

## 3. AI Token 成本

### 🔴 C1 · v2 `_pipeline_analyze` 无条件写 `analysis:*` 缓存，且会缓存降级结果
- **根因**：`ai/analyzer.py:167-227`（尤其 `:205-207`）从不读 `get_analysis_cache` 却无条件 `set_analysis_cache`，且写入的 `text` 可能是降级骨架/兜底文案。该 key 仅被 legacy 路径读取 → 跨流污染（legacy 读到 v2 降级报告），且降级结果被缓存复用。
- **方案**：v2 不写 `analysis:*`（或用独立命名空间 `direct:*`）；写入前校验非降级、非空。
- **预期效果**：消除"缓存到坏报告"的隐性质量事故。

### 🔴 C2 · DIRECT 默认流满配跑（最贵默认）
- **根因**：`config/settings.py:68 deepseek_analysis_effort="max"` + `:72-75` 思考默认 enabled + `:60 max_tokens=24576`。思考 token 按输出计价，是最大成本项；DIRECT 一次成文本无需最深推理。
- **方案**：默认改 `effort="high"`；DIRECT 默认 `thinking_enabled=false`（需要时显式开）。保留 env 覆盖。
- **预期效果**：真实分析 token 成本预计 **降 40–60%**（思考 token 是主要成本），质量在 direct.md 强约束下基本无感。
- ⚠️ 行为变更项，需你确认。

### 🟡 C3 · effort=low/medium 静默映射 high
- **根因**：`settings.py:199-203` 仅告警不拦截，API 静默按 high 计费——既没降本又误导配置。
- **方案**：校验时归一为 high 或直接拒绝并提示只支持 high/max。

### 🟡 C4 · self_check 逐条 mismatch 各开一次 LLM
- **根因**：`ai/…/self_check_rewrite.py:45-54` + `_helpers.py:161-162` 每条 mismatch 一次 LLM 调用，各自新开线程 + 事件循环 + HTTP client。DIRECT 实际成本 = 主调用 + N 次重写。
- **方案**：把多条 snippet 合并成单次批量改正；复用主循环的 client。

### 🟡 C5 · 废弃模型名仍在允许集
- **根因**：`settings.py:20-24` `deepseek-chat`/`deepseek-reasoner` 仍在 `_KNOWN`，仅告警不阻断；旧 .env 残留会走更贵/已下线模型并静默降级。
- **方案**：设弃用到期日，之后从允许集移除并硬报错。

### 🟡 C6 · 系统前缀提示词复述膨胀
- **根因**：DIRECT 系统提示 = `domain_knowledge.md` + `direct.md`（`phases.py:54-57`），二者在"北向/引用纪律/幻觉陷阱"大量复述 → 系统前缀 token 膨胀（虽命中前缀缓存，但每次仍占输入体积）。
- **方案**：去重——domain_knowledge 只留铁律，direct.md 引用不复述。

### 🟢 C7 · 次要项
- `analyzer.py:39,239-257` `_MAX_TOOL_ROUNDS=0` → `range(0)` 死分支（legacy 开关，建议显式标注或删）。
- two-phase 下 `tables_md` 在 ANALYSIS 与 COMPILE user 消息各发一次（`orchestrator.py:554-562 vs 667-690`），重复输入 token。
- ✅ 已确认：前缀缓存设计正确（system 全静态 md，变量数据在 user 消息）；思考开关已读 `cfg.thinking_enabled`（无硬编码 True）。

---

## 4. 配置 / 文档 / 死代码 一致性

### 🔴 D1 · 文档数据源描述与代码脱节
- `README.md:32` 写"财务数据 = pysnowball"，实际 `cli/pipeline.py:23` 注入 `AkshareFinancialAdapter`（三大表已改 akshare，pysnowball 仅剩资金流向）。
- `AGENTS.md:90` flow 仍写 `financial(pysnowball)`；`AGENTS.md:96 / CLAUDE.md:125` "pysnowball 需 XUEQIU_TOKEN"过时。
- `AGENTS.md:101` 引用 `ai/data_cleaner.py`——**该文件不存在**（清洗逻辑在 `sentiment.py`/`web_search_tool.py`/`context_renderer`）。
- `CLAUDE.md:121` "Scoring 11-factor 加权模型"描述的是**不存在的模块**（真实为 `validation/integrity_checker.py` 逐维置信评分），且与 `CLAUDE.md:72` 自注"scoring.py 不存在"自相矛盾。
- **方案**：统一订正上述文档，与实际 akshare/integrity_checker 链路对齐。

### 🟢 D2 · 次要项
- `settings.py` 废弃模型名（同 C5）。
- `pyproject.toml:28 akshare>=1.16.0` 无上限，接口易变，建议加兼容上限。
- `ai/cache.py`（get/set_analysis_cache/skeleton_cache）缺独立单测（其余估值/对账/缓存/兜底覆盖良好）。
- `docs/superpowers/` 约 20 个历史 plan/spec md，冗余度高，建议归档。
- ✅ 已确认 `peer_compare` 是活代码（`orchestrator.py:309,345` 真实调用），集成测试标记正确。

---

## 5. 实施路线图（按批次，可独立验收）

> 每批完成后跑：`uv run --no-sync ruff check src/` + `uv run --no-sync mypy src/aimoon/` + `uv run --no-sync pytest -m "not integration"`，保持非集成套件全绿。

**批次 A · 报告质量与正确性（低风险，先做）**
- R1 重写 mock 文案（去幻觉+标注示例）+ mock 填充附录/可信度
- R2 仪表盘 clamp bug + 跨源一致性提示
- R3 附录死锚点/死 CSS 复活
- R4 ¥ 统一
- 预期：报告观感与可信度立竿见影提升，零行为风险。

**批次 B · Pipeline 性能（中风险）**
- P1 缓存 get/set 异步化
- P2 消除重复拉取（segment_revenue/footnotes）
- P3 quote/pysnowball 并入 gather
- P4 收窄 except
- 预期：真实端到端墙钟 -20~35%。

**批次 C · AI Token 成本（含行为变更，需确认）** ✅ 已落地（2026-07-16 晚间）
- C2 默认关思考 + effort=high ⚠️（最大降本项，改默认行为）→ 已落地
- C1 v2 停止污染 analysis:* 缓存 → 已落地
- C3/C5 effort 与废弃模型硬校验 → 已落地（C5 升级为日期感知告警）
- C4 self_check 批量化 → 已落地（`_run_rewrite_llm_batch`，单线程+单循环+单 client + gather 并发）
- C6 提示词去重 → 已落地（删 domain_knowledge §五 重复/冲突段）
- C7 评估：`_MAX_TOOL_ROUNDS=0` 为有意功能开关保留；two-phase `tables_md` 重复仅选配路径、改动风险>收益，仅记录不动
- 预期：真实 token 成本 -40~70%（实测验证待真实跑数）。

**批次 D · 文档/死代码清理（零风险收尾）** ✅ 已落地（2026-07-16 晚间）
- D1 文档订正（README/AGENTS/CLAUDE：财务源 akshare、社交清洗 post_processor、评分 integrity_checker）→ 已落地
- D2：C5 废弃模型名已落地；补 `tests/test_ai_cache.py`（analysis/skeleton 双键隔离防碰撞回归）；akshare 版本上限建议项未改（易变接口风险，超出优化范围）

---

## 6. 预期整体收益

| 指标 | 现状 | 优化后（预期） |
|---|---|---|
| 真实分析 token 成本 | 满思考 + effort=max | **-40~70%** |
| 端到端墙钟时间 | 缓存同步阻塞 + 重复拉取 + 部分串行 | **-20~35%** |
| 报告可信度硬伤 | 仪表盘误判 / 幻觉 / 空附录 / 无¥ | 基本清零 |
| 文档准确度 | 多处与代码脱节 | 对齐 |
| 隐性质量事故 | 会缓存降级报告 | 消除 |

---

*本报告为诊断阶段产物。批次 A/B/D 属低/零风险，可直接落地；批次 C 的 C2（关默认思考）会改变默认成本/质量取舍，落地前需你确认。*

**落地状态（2026-07-16 收尾）**：A / B / C / D 四批全部完成。最终质量门禁：`ruff check src/` ✅、`mypy src/aimoon/` ✅、`pytest -m "not integration"` = **297 passed / 3 deselected**。本机会跑真实分析需 `ai_provider`/API key 就绪（当前 `.env` 激活 longcat 且 `LONGCAT_THINKING_ENABLED=false`）；token 成本 -40~70% 与墙钟 -20~35% 为架构层预期，建议用 `aimoon 600519` 真实跑一次做数字验证。*
