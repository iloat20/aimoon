# aimoon 架构说明

## 概述

aimoon 采用 **六边形架构（Hexagonal Architecture / Ports & Adapters）** 思想，结合 **领域驱动设计（DDD）** 的核心概念。架构追求极简，每个模块只做一件事，依赖显式注入，核心业务逻辑可脱离任何框架进行单元测试。

```
                    ┌──────────────────────────────────────────┐
                    │         Driving Adapters (驱动端)         │
                    │  CLI entry point  ·  PipelineOrchestrator│
                    └─────────────────────┬────────────────────┘
                                          │
                    ┌─────────────────────▼────────────────────┐
                    │        Application Layer (Core)          │
                    │  services/  ·  ports/ (输出端口)          │
                    │  — 只做编排，不包含业务规则               │
                    └─────────────────────┬────────────────────┘
                                          │
                    ┌─────────────────────▼────────────────────┐
                    │          Domain Layer (Core)             │
                    │  聚合根  ·  实体  ·  值对象              │
                    │  领域服务  ·  资源库端口(输入端口)        │
                    │  — 纯业务逻辑，无 IO                     │
                    └─────────────────────┬────────────────────┘
                                          │
                    ┌─────────────────────▼────────────────────┐
                    │        Driven Adapters (被驱动端)         │
                    │  collectors  ·  AI  ·  report  ·  validation │
                    │  config  ·  financial  ·  common         │
                    └──────────────────────────────────────────┘
```

**依赖方向：由外向内，指向核心。** 适配器依赖核心，核心不知道适配器的存在。

---

## 核心层 (Core)

### 1. Domain Layer（领域层）—— `src/aimoon/core/domain/`

**核心业务逻辑所在，最稳定、最纯洁的一层。只依赖 Pydantic 和标准库。**

#### 聚合根 (Aggregate Root)

- **`aggregates/stock_analysis.py`** — `StockAnalysis` 聚合根
  - 单只股票所有分析数据的一致性边界
  - 外部只能通过聚合根访问内部数据
  - 包含：quote, financial, kline, capital_flow, social_posts, research, annual_report, semi_annual_report, quarterly_report

**一致性边界说明：** StockAnalysis 是唯一的聚合根，所有维度数据都挂在它下面。这样做的原因：
1. 股票分析是一个完整的业务概念，各维度数据需要作为一个整体被处理
2. 评分逻辑需要同时访问多个维度的数据
3. 报告生成需要完整的数据视图
4. 数据采集虽然并行，但最终汇总为一个聚合实例

#### 实体 (Entities)

有唯一标识（symbol）的领域对象，放在 `entities/` 下：

- `quote.py` — `StockQuote`（行情数据）
- `financial.py` — `FinancialData`（财务数据）
- `kline.py` — `KlineData`（K 线数据）
- `capital_flow.py` — `CapitalFlowData`（资金流向数据）
- `research.py` — `ResearchReportData`（研报数据）
- `social.py` — `SocialPost`（社交媒体帖子）

#### 值对象 (Value Objects)

无唯一标识、不可变的对象，放在 `value_objects/` 下：

- `kline_bar.py` — `KlineBar`（单根 K 线）
- `dimension_score.py` — `DimensionScore`（维度评分）
- `analysis_report.py` — `AnalysisReport`（分析报告）
- `collect_result.py` — `CollectResult`（采集结果）
- `financial_report.py` — `FinancialReportData`（财报数据）

#### 领域服务 (Domain Services)

纯函数，无副作用，放在 `services/` 下：

- `symbols.py` — 符号解析：`resolve_market()`、`resolve_symbol()`（注: 文档曾描述的 `scoring.py` 评分模块并不存在）

#### 资源库端口 (Repository Ports)

数据访问的抽象接口，放在 `repositories/` 下：

- `stock_analysis_repo.py` — `StockAnalysisRepository` ABC
  - `collect_all(symbol, name) -> StockAnalysis`：采集所有数据，返回完整聚合
  - `get_collect_results() -> list[CollectResult]`：获取各平台采集结果

**规则：**
- 不依赖任何第三方库（除 Pydantic 用于模型定义）
- 只能使用 Python 标准库和同层模块
- 所有业务规则在这里定义

---

### 2. Application Layer（应用层）—— `src/aimoon/core/application/`

**"系统做什么"的编排层，不关心"怎么做"。**

#### 应用服务 (Application Services)

函数式的用例编排，放在 `services/` 下：

- `stock_analysis_service.py`
  - `collect_and_analyze(symbol, name, repo, ai_analyzer, data_validator, report_generator, ...)`：完整流水线
  - 所有依赖通过函数参数显式注入

#### 输出端口 (Output Ports)

应用层定义的、由适配器实现的接口，放在 `ports/` 下：

- `ai_analyzer.py` — `AIAnalyzer` ABC
- `data_validator.py` — `DataValidator` ABC
- `report_generator.py` — `ReportGenerator` ABC

**规则：**
- 只能依赖 domain 层
- 不依赖任何适配器
- 通过端口（接口）与外部交互，**依赖倒置原则**
- 应用服务只做编排，不包含具体算法和业务规则

---

## 适配器层 (Adapters)

### 1. Driving Adapters（驱动适配器 / 输入端）—— `src/aimoon/adapters/driving/`

**驱动应用运行的适配器，位于架构的"左"侧。**

- **`cli/main.py`** — CLI 入口，参数解析
- **`cli/pipeline.py`** — `PipelineOrchestrator`，组装所有依赖，调用应用服务

**职责：**
- 接收用户/外部系统的输入
- 组装所有被驱动适配器的实例（手动依赖注入）
- 调用应用服务
- 不包含业务逻辑

---

### 2. Driven Adapters（被驱动适配器 / 输出端）—— `src/aimoon/adapters/driven/`

**实现核心层端口的适配器，位于架构的"右"侧。**

- **`collectors/`** — 数据采集适配器
  - `composite_repo.py` — `CompositeStockAnalysisRepository`，组合多个 collector 实现资源库接口
  - `quote.py`、`kline.py`、`capital_flow.py` 等 — 各维度采集器
  - `social_orchestrator.py` — 社交媒体采集编排
  - `mock.py` — Mock 数据生成器
  - `base.py` — 采集器基类和注册机制

- **`ai/`** — AI 分析适配器
  - `analyzer.py` — `AIAnalyzer`，实现 `AIAnalyzer` 端口
  - `prompts.py` — 提示词模板

- **`report/`** — 报告生成适配器
  - `generator.py` — `ReportGenerator`，实现 `ReportGenerator` 端口
  - `templates/` — HTML 模板和 CSS

- **`validation/`** — 数据验证适配器
  - `integrity_checker.py` — `IntegrityDataValidator`，实现 `DataValidator` 端口

- **`financial/`** — 财务数据适配器
  - `akshare_adapter.py` — akshare(东方财富)财务数据

- **`config/`** — 配置适配器
  - `settings.py` — Pydantic Settings 配置加载

- **`common/`** — 通用工具
  - `retry.py` — 重试、静默失败
  - `parsers.py` — 中文数字解析、URL 提取

**规则：**
- 实现核心层定义的端口接口
- 可以依赖 core 层的所有模块
- 所有外部库（akshare、httpx、playwright 等）只在这里使用
- 不包含业务规则，只做技术实现

---

## 设计决策与备选方案

### 决策 1：函数式应用服务 vs 类式 UseCase

**选择：函数式应用服务**

备选方案：
- **类式 UseCase（如 `AnalyzeStockUseCase` 类）**：
  - 优点：更符合传统 DDD 表述，状态管理方便
  - 缺点：对于无状态的编排场景属于过度设计，需要写更多样板代码
- **函数式（当前选择）**：
  - 优点：极简，依赖注入一目了然，纯函数易测试
  - 缺点：如果未来需要复杂状态管理可能需要重构为类

选择理由：当前用例编排是无状态的，函数式足够且更简洁。遵循"极简"原则。

### 决策 2：统一资源库接口 vs 按维度端口

**选择：统一资源库接口（Composite Repository 模式）**

备选方案：
- **按维度端口（QuoteProvider、KlineProvider 等 7-8 个接口）**：
  - 优点：接口职责单一，替换单个数据源方便
  - 缺点：接口数量多，应用层需要注入 7-8 个依赖，编排复杂
- **统一资源库接口 + Composite 模式（当前选择）**：
  - 优点：应用层只需要一个依赖，接口简洁
  - 缺点：单个资源库接口较"粗"，替换单个数据源需要改 Composite 内部

选择理由：应用层只关心"获取完整的股票分析数据"，不关心数据来自哪里。Composite 模式在适配器层内部处理多数据源的复杂性，对应用层透明。

### 决策 3：Pydantic 模型 vs 纯 dataclass

**选择：Pydantic 统一模型**

备选方案：
- **纯 dataclass 领域模型 + Pydantic DTO（双层模型）**：
  - 优点：领域层完全不依赖第三方库，更"纯净"
  - 缺点：需要维护两套模型 + 转换代码，重复代码多，容易出错
- **Pydantic 统一模型（当前选择）**：
  - 优点：一套模型通吃，序列化/验证开箱即用，代码量少
  - 缺点：领域层依赖 Pydantic（但 Pydantic 是纯 Python 库，无 IO）

选择理由：Pydantic 是纯数据建模库，不引入 IO 或框架耦合。用"减少 50% 代码量"换取"依赖一个纯数据库"是合理的权衡。领域层的核心要求是"可脱离 HTTP/数据库测试"，而不是"零第三方依赖"。

---

## 如何扩展

### 添加新的数据源

1. 在 `adapters/driven/collectors/` 中创建新的采集器类，继承 `BaseCollector`
2. 在 `CompositeStockAnalysisRepository` 中注册并调用新采集器
3. 如果是新的数据维度，在 `core/domain/entities/` 或 `value_objects/` 中添加对应模型
4. 在 `StockAnalysis` 聚合根中添加新字段

### 添加新的分析维度

1. 在 `core/domain/services/` 中添加评分逻辑（纯函数）——注: 当前 `scoring.py` 并不存在,评分功能尚未实现
2. 在 `AnalysisReport` 值对象中添加新的维度字段
3. 在应用服务中调用新评分函数
4. 更新报告模板展示

### 替换 AI 服务商

1. 在 `adapters/driven/ai/` 中创建新的实现类，继承 `AIAnalyzer` 端口
2. 在 `PipelineOrchestrator` 的依赖组装处替换实现
3. 核心层（domain + application）完全不需要改动

---

## 命名指南

- **端口接口**：名词描述能力，如 `StockAnalysisRepository`、`AIAnalyzer`、`ReportGenerator`
- **应用服务**：动词短语函数名，如 `collect_and_analyze`
- **领域服务**：动词短语函数名，描述做什么，如 `fundamental_score`
- **适配器实现**：具体技术 + 描述，如 `CompositeStockAnalysisRepository`、`IntegrityDataValidator`
- **聚合根**：业务概念名，如 `StockAnalysis`
- **实体**：业务概念名，如 `StockQuote`、`FinancialData`
- **值对象**：描述性名词，如 `KlineBar`、`DimensionScore`

---

## 测试策略

| 层级 | 测试方式 | 是否需要外部依赖 |
|------|---------|----------------|
| Domain | 单元测试 | 不需要，纯函数直接测 |
| Application | 单元测试 | 不需要，Mock 所有端口 |
| Adapters (driven) | 集成测试 | 需要，调用真实 API |
| Adapters (driving) | 端到端测试 | 需要，完整流程 |

**领域层和应用层的测试可以在不安装 akshare/playwright、不配置 API Key 的环境下运行。**

---

## 极简原则

架构遵循以下极简原则：

1. **每层只做一件事**：领域层只管业务规则，应用层只管编排，适配器只管技术实现
2. **显式依赖注入**：所有外部依赖通过函数参数传入，无全局单例
3. **最少的抽象**：只在真正需要解耦的地方引入接口，不为抽象而抽象
4. **函数优先**：能用函数解决的就不用类，能用简单类解决的就不用复杂框架
5. **破坏性重构**：不保留向后兼容层，避免技术债累积
