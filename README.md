# Aimoon — AI A股分析工具

输入股票代码，自动完成 **采集 → 整合 → AI分析 → 可视化报告** 四步流程。

```bash
# 安装（二选一，v0.5.0 已发布到 PyPI）
pip install aimoon              # 方式一：从 PyPI 直接安装（推荐）
cd aimoon && uv sync && pip install -e .   # 方式二：从源码安装（开发/贡献用）

# 配置（编辑 .env 填入 API Key）
cp .env.example .env

# 首次使用需安装 Playwright 浏览器（股吧/头条/微信采集用）
uv run playwright install chromium

# 分析任何A股
aimoon 600519                  # 贵州茅台（真实数据）
aimoon 600519 --mock           # Mock模式（无需API Key）
aimoon 600519 --test           # 测试模式（采集真实数据，跳过AI分析）
aimoon test 600519             # 同 --test
aimoon 000001                  # 平安银行
aimoon 000858 -o ./reports     # 五粮液，指定输出目录
aimoon 600519 --fast           # 快速模式(跳过自检+COMPILE,直接出初稿)
aimoon 600519 --mock --fast    # Mock + 快速组合
```

---

## 数据采集覆盖

| 数据源 | 采集方式 | 数据量 | 状态 |
|--------|----------|--------|------|
| 实时行情(含PE) | 雪球 stock.xueqiu.com API | — | ✅ |
| 财务数据 | akshare（三大表 + 新浪兜底）+ 巨潮年报 PDF 附注解析 | 年报+季报 | ✅ |
| K线历史 | akshare (前复权日线) | 120根 | ✅ |
| 资金流向 | pysnowball + akshare + 东方财富(北向) | — | ✅ |
| 机构研报 | akshare (东方财富) | 最近一年 | ✅ |
| 最新年报/半年报/季报 | 巨潮资讯 API | 缓存30天 | ✅ |
| 东方财富股吧 | Playwright | 15条 | ✅ |
| 巨潮资讯·公司公告 | 官方API (股票名称搜索) | 20条 | ✅ |
| 微信公众号 | 搜狗微信搜索 (Playwright绕反爬) | 20条 | ✅ |
| 今日头条 | Playwright 搜索 | 18-20条 | ✅ |

> ✅ = 开箱即用 &emsp; ⚠️ = 需手动配置 &emsp; 🚧 = 开发中

---

## 安装指南

### 前置依赖

- **Python ≥ 3.12**
- **uv**（Python包管理器）: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
- **Chrome 浏览器** (Playwright 需要)

### 基础安装

**方式一：从 PyPI 安装（推荐，无需克隆仓库）**

```bash
pip install aimoon
uv run playwright install chromium   # 仅首次需安装浏览器
```

**方式二：从源码安装（开发 / 贡献）**

```bash
# 1. 进入项目目录
cd aimoon

# 2. 安装依赖
uv sync

# 3. 安装 CLI 工具（全局可用）
pip install -e .

# 4. Playwright 浏览器
uv run playwright install chromium
```

> **提示**: 也可用 `uv tool install --editable .` 替代 `pip install -e .`，两者等效。

### 升级

```bash
# PyPI 安装用户
pip install --upgrade aimoon

# 源码安装用户
git pull
uv sync
pip install -e .             # 重新安装 CLI
```

### 卸载

```bash
uv tool uninstall aimoon
```

### 配置文件 (.env)

```ini
# === 必配 ===
AI_PROVIDER=deepseek              # AI 提供商：deepseek（默认）| longcat
DEEPSEEK_API_KEY=sk-xxx          # DeepSeek API Key（--test 模式可跳过）
LONGCAT_API_KEY=sk-xxx           # LongCat API Key（AI_PROVIDER=longcat 时必填）

# === 雪球（推荐配置，可获取含PE的实时行情 + 财报 + 雪球热帖）===
XUEQIU_TOKEN=xq_a_token=xxx; u=xxx
XUEQIU_COOKIE=xq_a_token=xxx; u=xxx

# === 可选 ===
DEEPSEEK_BASE_URL=https://api.deepseek.com   # 自定义 DeepSeek API 地址
LONGCAT_THINKING_ENABLED=false               # LongCat 思考模式开关（关掉可显著省成本）
MOCK_MODE=false                               # 设为 true 等同 --mock
CACHE_DIR=./cache                             # 数据缓存目录
```

> 雪球 Cookie 获取: 浏览器登录 [xueqiu.com](https://xueqiu.com) → F12 → Application → Cookies → 复制 `xq_a_token` 和 `u`

---

## 命令行选项

| 命令 | 说明 |
|------|------|
| `aimoon <code>` | 完整分析（采集 + AI分析 + 报告） |
| `aimoon <code> --mock` | 全模拟数据，无需任何 API Key |
| `aimoon <code> --test` | 采集真实数据，跳过 AI 分析 |
| `aimoon test <code>` | 同 `--test` |
| `aimoon <code> -o <dir>` | 指定报告输出目录 |
| `aimoon --version` | 查看版本号 |

**股票代码规则**: 6开头 → 上海(SH)，0/3开头 → 深圳(SZ)，4/8开头 → 北交所(BJ)

---

## 架构

采用 **六边形架构（Ports & Adapters）** + DDD 分层设计：

```
                    ┌──────────────────────────────────────────┐
                    │           Adapters (Driving)            │
                    │  CLI entry point  ·  PipelineOrchestrator│
                    └─────────────────────┬────────────────────┘
                                          │
                    ┌─────────────────────▼────────────────────┐
                    │        Application Layer (Core)          │
                    │  services/  ·  ports/ (output ports)     │
                    │  — orchestration only, no business logic │
                    └─────────────────────┬────────────────────┘
                                          │
                    ┌─────────────────────▼────────────────────┐
                    │          Domain Layer (Core)             │
                    │  aggregates  ·  entities  ·  value objs  │
                    │  domain services  ·  repository ports    │
                    │  — pure business logic, no IO            │
                    └─────────────────────┬────────────────────┘
                                          │
                    ┌─────────────────────▼────────────────────┐
                    │          Adapters (Driven)               │
                    │  collectors  ·  AI analyzer  ·  report   │
                    │  validation  ·  config  ·  financial     │
                    └──────────────────────────────────────────┘
```

### 分层结构

- **`core/domain/`** — 领域模型（无外部依赖，仅 Pydantic）
  - `aggregates/stock_analysis.py` — StockAnalysis 聚合根
  - `entities/` — 实体：quote, financial, kline, capital_flow, social, research
  - `value_objects/` — 不可变值对象：KlineBar, DimensionScore, AnalysisReport, CollectResult, FinancialReport
  - `services/` — 纯领域服务：symbol resolution（注：文档曾描述的 `scoring.py` 评分模块实际并不存在）
  - `repositories/` — 资源库接口（数据访问输入端口）

- **`core/application/`** — 应用层（仅编排）
  - `services/stock_analysis_service.py` — `collect_and_analyze()` 主用例函数
  - `ports/` — 输出端口接口：AIAnalyzer, DataValidator, ReportGenerator

- **`adapters/driving/`** — 驱动适配器（输入侧）
  - `cli/main.py` — CLI 入口
  - `cli/pipeline.py` — PipelineOrchestrator，组装所有适配器

- **`adapters/driven/`** — 被驱动适配器（输出侧）
  - `collectors/` — 数据采集适配器（Composite Repository 模式）
  - `ai/` — AI 分析适配器（提供商无关：DeepSeek / LongCat，支持 Tool Calling + 流式输出）
  - `report/` — HTML 报告生成器（Jinja2）
  - `validation/` — 数据完整性校验
  - `financial/` — 财务报告适配器
  - `config/` — 配置适配器

### 关键设计决策

- **函数式应用服务** — 无类式 UseCase，纯函数 + 显式依赖注入
- **Composite Repository** — `CompositeStockAnalysisRepository` 组合多个采集器，统一对外提供 `StockAnalysisRepository` 端口
- **统一 Pydantic 模型** — 单一模型层，无 dataclass/Pydantic 双系统
- **依赖方向**: `core/` 从不导入 `adapters/`，所有依赖指向内部

### 评分模型

11 因子评分（1-5 分），3 维度加权：

| 维度 | 权重 | 说明 |
|------|------|------|
| 基本面 | 50% | ROE、营收同比、净利润同比 |
| 资金面 | 25% | 主力净流入、北向变化、龙虎榜 |
| 新闻舆情 | 25% | 机构研报买入/增持占比 |

---

## 项目结构

```
src/aimoon/
├── __init__.py                    # 版本号（从 package metadata 读取）
├── adapters/
│   ├── driving/                   # 驱动适配器（输入侧）
│   │   └── cli/
│   │       ├── main.py            # CLI 入口
│   │       └── pipeline.py        # PipelineOrchestrator — 流程编排
│   └── driven/                    # 被驱动适配器（输出侧）
│       ├── collectors/            # 数据采集器
│       │   ├── base.py            # 采集器基类 + 注册表
│       │   ├── quote.py           # 行情（雪球→新浪→腾讯 三级兜底）
│       │   ├── kline.py           # K线历史（akshare）
│       │   ├── capital_flow.py    # 资金流向（pysnowball + akshare + 东方财富）
│       │   ├── research_report.py # 机构研报（东方财富，最近一年）
│       │   ├── eastmoney_playwright.py # 东方财富股吧 (Playwright, 15条)
│       │   ├── cninfo.py         # 巨潮资讯·公司公告 (20条)
│       │   ├── wechat.py         # 微信公众号（搜狗搜索 Playwright, 20条）
│       │   ├── toutiao.py        # 今日头条（Playwright, 18-20条）
│       │   ├── social_orchestrator.py # 社交采集编排器
│       │   ├── composite_repo.py # 组合资源库（统一对外接口）
│       │   ├── mock_repo.py      # Mock 数据资源库
│       │   └── mock.py           # Mock 数据生成器
│       ├── ai/                    # AI 分析引擎（多提供商）
│       │   ├── analyzer.py        # AIAnalyzer（提供商无关：DeepSeek / LongCat，Tool Calling + 流式）
│       │   ├── prompts.py         # Prompt 模板
│       │   ├── post_processor.py  # 社交文本清洗 + 报告后处理（数据清洗在此）
│       │   ├── web_search_tool.py # Web 搜索工具（Bing → DuckDuckGo 兜底）
│       │   └── pipeline/          # v2 AI 分析流水线（DIRECT / 骨架+扩写双流）
│       ├── report/                # 报告生成
│       │   ├── generator.py       # Jinja2 模板渲染
│       │   └── templates/index.html
│       ├── financial/             # 财务数据处理
│       │   ├── akshare_adapter.py  # akshare 适配器（三大表 + 新浪兜底）
│       │   ├── annual_report_pdf.py # 巨潮年报 PDF 下载 + pdfplumber 解析
│       │   └── parsing.py          # 报表字段解析工具
│       ├── validation/            # 数据质量
│       │   └── integrity_checker.py
│       ├── config/                # 配置
│       │   └── settings.py        # Pydantic-settings + .env
│       └── common/                # 共享工具
│           ├── browser.py         # Playwright 浏览器管理
│           ├── cache.py           # 缓存工具
│           ├── parsers.py         # 解析工具
│           └── retry.py           # 重试工具
└── core/
    ├── domain/                    # 领域层（纯业务逻辑）
    │   ├── aggregates/
    │   │   └── stock_analysis.py  # StockAnalysis 聚合根
    │   ├── entities/              # 实体（quote, financial, kline 等）
    │   ├── value_objects/         # 值对象（DimensionScore, AnalysisReport 等）
    │   ├── services/              # 领域服务
    │   │   └── symbols.py         # 股票代码解析
    │   └── repositories/          # 资源库接口（端口）
    │       └── stock_analysis_repo.py
    └── application/               # 应用层（编排）
        ├── services/
        │   └── stock_analysis_service.py  # collect_and_analyze() 主用例
        └── ports/                 # 输出端口接口
            ├── ai_analyzer.py
            ├── data_validator.py
            └── report_generator.py
```

## 输出示例

生成的 HTML 报告包含：

- **头部**：股票代码、实时行情（价格/涨跌/PE）、当前时间
- **三列卡片**：基本面（含数据来源如"2026一季报"）/ 资金面 / 估值百分位
- **K线走势图**：近120日收盘价折线图 + 成交量柱状图（Chart.js 交互式图表）
- **全网热度**：各平台帖子列表（15-20条，含点赞/评论/链接）
- **机构研报**：评级分布、EPS预测、PDF下载链接（最近一年）
- **AI 综合分析报告**：默认 DeepSeek v4-flash 深度思考（可切换 LongCat），支持 Web 搜索工具调用
- **执行摘要 + 估值情景卡片**：报告顶部附评级/仓位建议，以及乐观🟢/中性🟡/悲观🔻三档安全边际测算
- **月度监测清单 + Red Team 压力测试**：跟踪关键指标阈值，并给出极端情景下的风险推演
- **亮色/暗色主题切换**：右上角按钮一键切换，状态自动保存
- **数据来源清单**：每个平台采集状态

报告为纯静态 HTML，可直接通过浏览器打开或分享。

---

## 开发

```bash
uv sync --group dev          # 安装开发依赖（pytest + ruff + bandit + pylint）
uv run ruff check src/       # Lint 检查 (line-length 100)
uv run mypy src/aimoon/      # 类型检查
uv run pytest                # 运行测试
```

---

## 免责声明

本报告由 AI 自动生成，所有分析内容仅供参考，不构成任何投资建议。投资有风险，入市需谨慎。
