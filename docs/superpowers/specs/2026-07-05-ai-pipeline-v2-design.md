# AI 分析 pipeline v2 设计

- **日期**: 2026-07-05
- **状态**: 待审阅
- **涉及模块**: `adapters/driven/ai/analyzer.py`, `adapters/driven/collectors/financial.py`, `adapters/driven/collectors/kline.py`, `adapters/driven/common/retry.py`
- **关联需求**: 解决 AI 分析忽深忽浅、结构不一致、看空逻辑流于形式、幻觉/重复/格式问题

## 1. 痛点(为什么改)

采集器最近风控告警(见 git 4c3d2408)虽已修复,但根因分析暴露了一个上层问题:**AI 分析的质量只靠一段式 LLM + 自主决策的单工具 `web搜索` 支撑**,表现为:

| 现象 | 根因 |
|---|---|
| 报告深度忽深忽浅 | 研究流程由模型自主决策,无强制覆盖结构 |
| 看空/财务三年趋势常常泛泛而谈 | 输入数据单薄,财务只给单期硬指标 |
| 幻觉、重复段落、表格格式乱 | 输出即终稿,没有校验闭环 |
| 邻股报告不可比 | 无阶段化模板,输出骨架不同 |

采集器修得再稳,研究内容和流程本身的天花板还在。本 spec 处理**流程和数据输入**这一层。

## 2. 设计决策序列(每条经用户确认)

1. [x] 痛点优先解**研究流程**(覆盖度/一致性),不是回测闭环或纯输入做厚
2. [x] 范围:**流程控制器 + 扩展工具集**(6 个新工具)
3. [x] 控制权:**状态机主导阶段顺序 + 模型保留 web_search 决策**
4. [x] 历史财务覆盖:**拉 3 年**,扩展 FinancialCollector
5. [x] 工具集**不过端口**(Ports),直接是模块内纯函数——无外部 IO 的业务稳定工具无需端口抽象

## 3. 流水线架构:五阶段模板化递进

```
StockAnalysis(聚合)
   │
   ▼
[1] PLAN      →  web_search(可选)                           → 研究大纲
   │
   ▼
[2] COLLECT   │ technicals ─┐
(并行)        │ financial_temporal ─┼ 3 个纯函数工具 + 模型可选 web_search
              │ peer_compare ──┘
   │
   ▼
[3] ANALYSIS  │ risk_quant → valuation / business_moat(可并行) + 模型可选 web_search
(串行)
   │
   ▼
[4] SELF_CHECK│ 纯 LLM 自检(无外部工具)                     → 结构化 JSON
   │
   ▼
[5] COMPILE   │ 复读 _stream_final_response                 → AnalysisReport
```

### 3.1 每阶段强制质量门(不通过重跑,最多 2 次 → 失败降级为 `[partial]`)

| 阶段 | 强制覆盖清单 |
|---|---|
| PLAN | 子任务 ≥8,覆盖"业务画像与护城河/财务健康诊断/交叉验证/估值与逆向视角" |
| COLLECT | 三工具全部非空;竞品 ≥3 家;财务时序 ≥3 年 |
| ANALYSIS | 3 条看空各含触发条件 + 估值冲击%;估值含保守/中性/乐观三档;业务含 SWOT 或护城河来源 |
| SELF_CHECK | 5 项 JSON 校验(a) 每个关键数字标注源 `训练数据/公司年报/搜索结果` (b) 三张核心表格格式合规 (c) 看空非泛泛(含触发条件)(d) 投资建议明确(买/持/卖 + 价格区间 + 催化剂)(e) 全文无重复段落 |
| COMPILE | 长 Markdown;注入 disclaimer;写磁盘缓存 |

### 3.2 向后兼容

- 旧 `_legacy_analyze()` 完整保留
- `analyze(..., use_pipeline_v2: bool = False)` 默认走旧链路
- 新链故障可零迁移成本回退

## 4. 扩展工具集(6 个新工具,统一返回 JSON)

**设计原则**:新工具尽量是**纯函数**,输入只来自 `StockAnalysis` 聚合。只有 `peer_compare` 因外部数据触发一次 `web_search`。工具无外部 HTTP → <10ms / 次、确定性、可单测。

| 工具 | 输入 | 输出 | 数据源 | 类型 |
|---|---|---|---|---|
| `technicals` | .kline.bars(180根) + .capital_flow | 5/10/20/60日均线 + MACD + RSI + 布林带 + 量比 + 主力净流入;"趋势=多头/空头/震荡"+支撑/阻力位 | 内算 | 纯函数 |
| `financial_temporal` | .history_financial:`Financial[]`(近3年) | 年度/季度 营收/净利/ROE/经营现金流 + 3 年 CAGR + 趋势判断 | **小扩采集器**(拉3年历史) | 纯函数 |
| `peer_compare` | .name + .financial | 同行业 3-5 家竞品对比表(市值/PE/PB/ROE/近3年CAGR + 相对位势)+ JSON `__needs_search__` 标记 | 内 + web_search | 组合 |
| `business_moat` | .research + .social + .financial.ocf 3年 | SWOT + 护城河来源 + OCF/利润含金量 + 上下游议价 | 内 + 少量 web_search | 组合 |
| `risk_quant` | financial_temporal + .quote | 3 看空(含触发条件 + 冲击%) + 3 看多 + 关键比率预警 | 内算 | 纯函数 |
| `valuation` | financial_temporal + .quote + peer_compare | PE/PB + FCFE 三档目标价 + 同业对比 | 内算 + 外部 g/r 参考 | 组合 |

**失败统一契约**(每个工具都遵守):不抛异常,失败返回 `{"__partial__": true, "reason": "<原因>"}`。上游据此决定是否降级。

## 5. 六角架构落位(依赖方向:控制向内)

```
core/application/ports/ai_analyzer.py   ← AIAnalyzer(抽象,不变)
          ▲
          │ 实现
adapters/driven/ai/
  ├── analyzer.py                 · analyze(use_pipeline_v2=...) 路由
  │                                · _legacy_analyze() 完全保留
  │                                · _pipeline_analyze() 新入口
  │                                · _stream_final_response() 复用
  ├── cache.py                    · 现 24h 磁盘缓存(不变)
  ├── web_search_tool.py          · 现 web_search(不变,被 orchestrator 组合)
  ├── data_cleaner.py             · 现(不变)
  ├── prompts/phases/             [新] 每阶段专属 system prompt
  ├── pipeline/
  │     ├── phases.py             · 五阶段 + 每阶段质量门
  │     ├── orchestrator.py       · 串联 phases + 状态机 + 重试 + 超时
  │     └── prompts/              [新] 每阶段 system prompt
  └── tools/
        ├── technicals.py         [新] 纯函数
        ├── financial_temporal.py [新] 纯函数
        ├── peer_compare.py       [新] 组合 web_search
        ├── business_moat.py      [新] 组合 web_search
        ├── risk_quant.py         [新] 纯函数
        └── valuation.py          [新] 纯函数

adapters/driven/collectors/
  └── financial.py                · 单期→加 history_financial 列表(扩展)
```

**Backward-compat 原则**:pipeline 是 `adapters/driven/ai/` 层的**新并行路径**,不改一行 `core/`、不改 AIAnalyzer 抽象定义、不改 stock_analysis_service 的编排。

**模块新增**:
- 新增: `pipeline/`, `tools/`, `prompts/phases/` (三个目录,约 8 个新源文件)
- 改造: `analyzer.py`(加路由入口 + pipeline orchestration 调用), `financial.py`(加 3 年历史入口)
- 保留原样: `cache.py`, `web_search_tool.py`, `data_cleaner.py`

## 6. StockAnalysis 聚合的扩展

仅新增可选字段,**无破坏**:

```python
# 在 StockAnalysis 现有字段后追加
history_financial: list[Financial] = field(default_factory=list)  # 近 3 年报
```

旧链路根本不读此字段,完全兼容。Collector 在采集阶段填之。

## 7. 缓存、失败降级、超时

### 7.1 三层缓存

| 层 | 存储 | Key 构成 | TTL | 用途 |
|---|---|---|---|---|
| L1 磁盘缓存 | `output/.cache/{symbol}.md` | 标的 + 日期 | 24h | 标的同一天直接返,完全跳过 pipeline(现 `cache.py`) |
| L2 阶段级内存缓存 | `pipeline/_phase_cache.py`(进程内) | `hash(symbol + 数据指纹(行情/财务/资金 hash) + 阶段名)` | 进程生命周期 | 同次调用内跨阶段复用(SELF_CHECK 多次循环等) |
| L3 工具级缓存 | 自身持有(可选) | 输入参数 hash | 工具定义内 | 技术指标同 bars 输入 → 直接返 |

数据指纹设计:**同一标的、同一行情**当日复跑不重算工具,但**行情变了**(盘中多次跑)自动重算。

### 7.2 故障降级链(单阶段独立)

- `technicals` 失败 → 返 `{"__partial__":"no_kline"}`,valuation 不引用
- `financial_temporal` 失败 → 仅用当期 FinancialData,SELF_CHECK 自动标记历史缺失
- `peer_compare` 失败(搜索双引擎断) → 跳过竞品对比表格,SELF_CHECK 标记
- `risk_quant` / `valuation` 失败 → 本章节标 `[partial: 计算异常]`

**每阶段最多 2 次尝试**,2 次均失败进入上述降级,**绝不阻塞后续阶段**(相对"当前一失败整个 AI 分析挂掉"是可靠性提升)。

### 7.3 超时硬上限 5 分钟

| 阶段 | 单阶段预算 | 累计 |
|---|---|---|
| PLAN | 30s | 30s |
| COLLECT | 60s(3 并行) | 90s |
| ANALYSIS | 120s | 210s |
| SELF_CHECK | 30s | 240s |
| COMPILE | 60s | **300s 总上限** |

超时后:**已完成阶段保留 + 未完成阶段占位符** → 仍输出报告,标 `[超时降级]`。当前无上限,实测极端 10min+。

## 8. 现有 settings 与配置复用

完全复用现有 `deepseek_*` 系列 settings,零新增配置项。

新增可选 settings(用于成本/阶段控制):

```python
PIPELINE_PHASES_ENABLED = {
    "plan": True,      # 最快见效: 直接用四框架可关掉,省一次 LLM 调用
    "collect": True,
    "analysis": True,
    "self_check": True,  # 最快见效单项,性高本但直接过滤幻觉
    "compile": True,
}
```

v1 全部开启,看数据后再调。

## 9. 成本/性能权衡

| 指标 | 当前(一段式) | pipeline v2 |
|---|---|---|
| LLM 调用次数 | 1-5次(循环)+ 1 次最终 | **6-7 次**(5 阶段 ×~1.3) |
| 工具调用 | ≤5 次 web_search | **3 纯工具 + ~2 web_search**(模型可加) |
| Token 消耗(估) | ~8-12k | ~15-20k |
| 时间 | 30-120s | 60-300s(均值 ~120s) |

成本上升主因:阶段拆分增多(系统提示 ×5)+ 输入更厚(历史财务 + 竞品)。换取:**3 个强制表格 + 看空触发条件 + 格式校验 + 重复兜底**。

## 10. 实现顺序(建议,非 spec 死约束)

### Phase 1:骨架 + 最快见效项
- `phases.py` 或 `orchestrator.py` 空壳(能串起来)
- `tools/financial_temporal.py` + `technicals.py`(纯函数,可先独立测)
- `financial.py`(拉 3 年历史)
- SELF_CHECK 阶段(成本最低、收益最直接——幻觉/重复/格式一次兜住)

**gate**:Historical 工具单元测试通过,SELF_CHECK 打通标的无崩溃

### Phase 2:研究深度
- 开通 PLAN + ANALYSIS 流程
- 接入 `peer_compare` / `risk_quant` / `valuation`
- SELF_CHECK 中对三张核心表格的格式校验细则

**gate**:600519 / 000001 / 601318 等标杆标出报告,人工 review 可达"分析可用"

### Phase 3:上线固定
- COMPILE 阶段接入 disclaimer + 缓存写入
- `use_pipeline_v2` 默认切 `True`(如有信心)
- 监控:每阶段耗时、partial 占比、SELF_CHECK 一次通过率

## 11. 验收标准

| 项 | 标准 |
|---|---|
| 达到"分析可用" | 600519 / 000001 等标杆标出报告,人工 review 可达"(非最差可用)" |
| 覆盖度 | 三张核心表格出现率 100%;看空触发条件非空率 100%(含 `[partial]` 标记) |
| 可靠性 | 单阶段失败不中断全链路;总耗时超 300s 仍有降级报告 |
| 向后兼容 | 旧链路 `use_pipeline_v2=False` 零回归 |
| cost 不失控 | SELF_CHECK 4 次重内稳定(不含人为刷新等反复) |
| test 覆盖 | 6 个工具的纯函数部分全部有单测;orchestrator 跑通标的无崩溃 |

## 12. 风险与应对

| 风险 | 应对 |
|---|---|
| 历史财务 3 年拉取被新浪风控/akshare 临时断 | financial_temporal 兜底为单期,SELF_CHECK 标历史缺失 |
| token 成本上升明显 | PIPELINE_PHASES_ENABLED 逐阶段可关,最快见效项为 self_check |
| 300s 总超时仍超标 | 阶段级 COLLECT 并行(已设),ANALYSIS 中 valuation / business_moat 并行 |
| prompt 跨阶段不连贯 | 质量门强制"阶段 N+1 必须引用阶段 N 的 X 字段" |
| 现 `_stream_final_response` 多次后缓冲残留 | ORCHESTRATOR 各阶段间显式清理 messages 缓冲(措施) |
| 测试覆盖不达 | Phase 1 强调 6 工具单测 + 标的无崩溃 双 gate |
