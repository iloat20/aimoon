# Aimoon — AI A股分析工具

输入股票代码，自动完成 **采集 → 整合 → AI分析 → 可视化报告** 四步流程。

```bash
# 安装
uv tool install --editable .

# 配置（编辑 .env 填入 API Key）
cp .env.example .env

# 分析任何A股
aimoon 600519                  # 贵州茅台（真实数据）
aimoon 600519 --mock           # Mock模式（无需API Key）
aimoon 600519 --test           # 测试模式（采集真实数据，跳过AI分析）
aimoon test 600519             # 同 --test
aimoon 000001                  # 平安银行
aimoon 000858 -o ./reports     # 五粮液，指定输出目录
```

---

## 数据采集覆盖

| 数据源 | 采集方式 | 数据量 | 状态 |
|--------|----------|--------|------|
| 实时行情(含PE) | 雪球 stock.xueqiu.com API | — | ✅ |
| 财务数据 | pysnowball (资产负债表/利润表/现金流) | — | ✅ |
| K线历史 | akshare (前复权日线) | 120根 | ✅ |
| 资金流向 | pysnowball + 东方财富(北向) | — | ✅ |
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

```bash
# 1. 进入项目目录
cd aimoon

# 2. 安装依赖
uv sync

# 3. 首次安装 CLI 工具（全局可用）
uv tool install --editable .

# 4. Playwright 浏览器
uv run playwright install chromium
```

### 升级

```bash
git pull
uv tool install --editable .  # 重新安装 CLI
```

### 卸载

```bash
uv tool uninstall aimoon
```

### 配置文件 (.env)

```ini
# === 必配 ===
DEEPSEEK_API_KEY=sk-xxx          # DeepSeek API Key（--test 模式可跳过）

# === 雪球（推荐配置，可获取含PE的实时行情 + 财报 + 雪球热帖）===
XUEQIU_TOKEN=xq_a_token=xxx; u=xxx
XUEQIU_COOKIE=xq_a_token=xxx; u=xxx

# === 可选 ===
DEEPSEEK_BASE_URL=https://api.deepseek.com   # 自定义 DeepSeek API 地址
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

```
用户输入股票代码 (600519)
     │
     ▼
┌─ ① 数据采集 ─────────────────────────────────────────────┐
│                                                           │
│  行情    → 雪球(含PE) → 新浪 → 腾讯   (三级兜底)          │
│  财报    → pysnowball                                     │
│  K线     → akshare (前复权日线120根)                       │
│  资金    → pysnowball + 东方财富(北向)                     │
│  研报    → akshare (东方财富，最近一年)                    │
│  年报    → 巨潮资讯 API (缓存30天)                         │
│  股吧    → Playwright (15条)                               │
│  公告    → 巨潮资讯 API (20条)                             │
│  微信    → 搜狗搜索 Playwright绕反爬 (20条)                │
│  头条    → Playwright (18-20条)                            │
│                                                           │
└───────────────────────┬───────────────────────────────────┘
                        ▼
┌─ ② 数据整合 + 质量校验 ──────────────────────────────────┐
│   - 各采集器按序执行，单个失败不影响全局                      │
│   - 格式校验 + 跨源交叉验证 + 时效性检查                    │
│   - 数据置信度评分（高/中/低）                               │
└───────────────────────┬───────────────────────────────────┘
                        ▼
┌─ ③ AI 分析 (DeepSeek v4-flash) ──────────────────────────┐
│   深度思考模式 · 当前时间注入 · 财务报告缓存读取            │
│   6维度: 情绪25% + 技术15% + 基本面20%                     │
│          资金15% + 新闻15% + 综合10%                       │
└───────────────────────┬───────────────────────────────────┘
                        ▼
┌─ ④ HTML 报告 (Jinja2 + Chart.js) ────────────────────────┐
│   深蓝暗色 · 响应式Grid · 红涨绿跌                          │
│   K线折线图 · 三列并排卡片 · 20条帖子展示                   │
│   纯静态HTML，可离线查看                                    │
└─────────────────────────────────────────────────────────────┘
```

## 项目结构

```
src/aimoon/
├── main.py              # CLI 入口 + 流程编排
├── config/settings.py   # 配置管理 (Pydantic + .env)
├── models/              # 数据模型
│   ├── stock.py         # StockQuote, FinancialData, KlineData, FinancialReportData
│   ├── social.py        # SocialPost, CollectResult
│   └── report.py        # AnalysisReport, DimensionScore
├── collectors/          # 数据采集器
│   ├── quote.py         # 行情（雪球→新浪→腾讯）
│   ├── kline.py         # K线历史（akshare）
│   ├── fund_flow.py     # 资金流向
│   ├── research_report.py # 机构研报（东方财富，最近一年）
│   ├── eastmoney_playwright.py # 东方财富股吧 (Playwright, 15条)
│   ├── cninfo.py        # 巨潮资讯·公司公告 (20条)
│   ├── wechat.py        # 微信公众号（搜狗搜索 Playwright, 20条）
│   └── toutiao.py       # 今日头条（Playwright, 18-20条）
├── financial/           # 财务数据
│   ├── pysnowball_adapter.py # pysnowball 适配器
│   └── annual_report.py      # 年报/半年报/季报获取与缓存(30天)
├── indicators/          # 技术指标
│   └── technical.py     # MA/MACD/KDJ/RSI/Bollinger
├── ai/                  # DeepSeek 分析引擎
│   └── analyzer.py      # DeepSeek v4-flash 深度思考模式
├── validation/          # 数据质量
│   └── integrity_checker.py
└── report/              # 报告生成
    ├── generator.py     # Jinja2 模板渲染
    └── templates/index.html
```

## 输出示例

生成的 HTML 报告包含：

- **头部**：股票代码、实时行情（价格/涨跌/PE）、当前时间
- **三列卡片**：基本面（含数据来源如"2026一季报"）/ 资金面 / 估值百分位
- **K线走势图**：近120日收盘价折线图 + 成交量柱状图（Chart.js 交互式图表）
- **全网热度**：各平台帖子列表（15-20条，含点赞/评论/链接）
- **机构研报**：评级分布、EPS预测、PDF下载链接（最近一年）
- **AI 综合分析报告**：DeepSeek v4-flash 深度思考模式生成
- **数据质量校验**：置信度评分
- **数据来源清单**：每个平台采集状态

报告为纯静态 HTML，可直接通过浏览器打开或分享。

---

## 开发

```bash
uv sync --group dev          # 安装开发依赖（pytest + ruff + bandit + pylint）
uv sync --extra selenium     # 启用东方财富股吧 Selenium 增强采集
uv run ruff check src/       # Lint 检查
uv run mypy src/aimoon/      # 类型检查
```

---

## 免责声明

本报告由 AI 自动生成，所有分析内容仅供参考，不构成任何投资建议。投资有风险，入市需谨慎。
