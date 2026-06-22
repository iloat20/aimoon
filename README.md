# Aimoon — AI A股分析工具

输入股票代码，自动完成 **采集 → 整合 → AI分析 → 可视化报告** 四步流程。

```bash
# 安装
cd aimoon
uv tool install --editable .

# 配置（编辑 .env 填入 API Key）
cp .env.example .env

# 分析任何A股
aimoon 600519                  # 贵州茅台（真实数据）
aimoon 600519 --mock           # Mock模式（无需API Key）
aimoon 600519 --test           # 测试模式（采集真实数据，跳过AI分析）
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
| 资金流向 | pysnowball / 同花顺 / 东方财富 | — | ✅ |
| 机构研报 | akshare (东方财富) | 100+ | ✅ |
| 雪球热帖 | HTTP API (hot list + stock search) | 10条 | ✅ |
| 东方财富股吧 | Playwright (优先) → akshare (兜底) | 10条 | ✅ |
| 巨潮资讯·公司公告 | 官方API + secCode二次过滤 | 20条 | ✅ |
| 微信公众号 | 搜狗微信搜索（含文章链接） | 10条 | ✅ |
| 今日头条 | Playwright 搜索 | 7-13条 | ✅ |
| 小红书 | Playwright + 登录态 | 7-10条 | ✅ |
| 抖音 | Playwright（反爬严格） | — | ⚠️ 待调试 |

> ✅ = 开箱即用 &emsp; ⚠️ = 需手动配置 &emsp; 🚧 = 开发中

---

## 安装指南

### 前置依赖

- **Python ≥ 3.12**
- **uv**（Python包管理器）: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
- **Chrome 浏览器** (Selenium/Playwright 需要)

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

### 配置文件 (.env)

```ini
# === 必配 ===
DEEPSEEK_API_KEY=sk-xxx          # DeepSeek API Key

# === 雪球（推荐配置，可获取含PE的实时行情 + 财报 + 雪球热帖）===
XUEQIU_TOKEN=xq_a_token=xxx; u=xxx
XUEQIU_COOKIE=xq_a_token=xxx; u=xxx
```

> 雪球 Cookie 获取: 浏览器登录 [xueqiu.com](https://xueqiu.com) → F12 → Application → Cookies → 复制 `xq_a_token` 和 `u`

### 小红书/抖音登录（可选）

```python
# 运行一次，会弹出浏览器窗口，扫码登录后按回车即可
uv run python -c "
from aimoon.collectors.xiaohongshu import XiaohongshuCollector
XiaohongshuCollector.login()
"
# 抖音同理
uv run python -c "
from aimoon.collectors.douyin import DouyinCollector
DouyinCollector.login()
"
```

> 登录态会保存到 `~/.aimoon/`，后续自动复用。

### 扩展工具（可选）

| 工具 | 用途 | 安装 |
|------|------|------|
| **Agent Reach** | 小红书/多平台接入 | `pip install agent-reach @ https://github.com/Panniantong/agent-reach/archive/main.zip` |
| **MediaCrawler** | 抖音/小红书专业采集 | `git clone https://github.com/NanmiCoder/MediaCrawler ~/.mediacrawler` |

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
│  资金    → pysnowball / 同花顺 / 东方财富                  │
│  研报    → akshare (东方财富)                              │
│  雪球    → HTTP热帖API + stock search                      │
│  股吧    → Playwright → akshare                            │
│  公告    → 巨潮资讯 API (searchkey + secCode)              │
│  微信    → 搜狗搜索（含文章链接）                          │
│  头条    → Playwright                                      │
│  小红书  → Playwright + 登录态                              │
│                                                           │
└───────────────────────┬───────────────────────────────────┘
                        ▼
┌─ ② 数据整合 ─────────────────────────────────────────────┐
│   - 9个采集器并发/串行执行                                 │
│   - 格式校验 + 跨源交叉验证 + 时效性检查                    │
│   - 单个采集器失败不影响全局                                │
└───────────────────────┬───────────────────────────────────┘
                        ▼
┌─ ③ AI 分析 (DeepSeek) ───────────────────────────────────┐
│   6维度: 情绪25% + 技术15% + 基本面20%                     │
│          资金15% + 新闻15% + 综合10%                       │
└───────────────────────┬───────────────────────────────────┘
                        ▼
┌─ ④ HTML 报告 (Jinja2 + Chart.js) ────────────────────────┐
│   深蓝暗色 · 响应式Grid · 红涨绿跌                          │
│   K线折线图 · SVG图表 · 评分环形图 · 情感堆叠条              │
│   纯静态HTML，可离线查看                                    │
└─────────────────────────────────────────────────────────────┘
```

## 项目结构

```
src/aimoon/
├── main.py              # CLI 入口 + 四步流程编排
├── config/settings.py   # 配置管理 (Pydantic + .env)
├── models/              # 数据模型
│   ├── stock.py         # StockQuote, FinancialData, KlineData
│   ├── social.py        # SocialPost, CollectResult
│   └── report.py        # AnalysisReport, DimensionScore
├── collectors/          # 12个采集器
│   ├── quote.py         # 行情（雪球→新浪→腾讯）
│   ├── kline.py         # K线历史（akshare）
│   ├── fund_flow.py     # 资金流向（同花顺→东方财富→雪球）
│   ├── research_report.py # 机构研报（东方财富）
│   ├── xueqiu.py        # 雪球热帖 + 含PE行情
│   ├── eastmoney_guba.py        # 东方财富股吧 (akshare)
│   ├── eastmoney_playwright.py    # 东方财富股吧 (Playwright)
│   ├── cninfo.py        # 巨潮资讯·公司公告
│   ├── wechat.py        # 微信公众号（搜狗搜索）
│   ├── toutiao.py       # 今日头条（Playwright）
│   ├── xiaohongshu.py   # 小红书（Playwright）
│   ├── agent_reach_wrapper.py   # AgentReach 接口
│   └── mediacrawler_adapter.py  # MediaCrawler 适配器
├── financial/           # 财报 (pysnowball)
│   └── pysnowball_adapter.py
├── indicators/          # 技术指标
│   └── technical.py     # MA/MACD/KDJ/RSI/Bollinger
├── ai/                  # DeepSeek 分析引擎
│   └── analyzer.py      # DeepSeekAnalyzer + AIAnalyzer
├── validation/          # 数据质量
│   ├── format_validator.py
│   ├── cross_validator.py
│   ├── freshness_checker.py
│   └── integrity_checker.py
└── report/              # 报告生成
    ├── generator.py     # Jinja2 模板渲染
    └── templates/index.html
```

## 输出示例

生成的 HTML 报告包含：

- **顶部**：股票代码、实时行情（价格/涨跌/PE）、综合评分环形图 + 摘要
- **6维度评分**：市场情绪/技术面/基本面/资金面/新闻舆情/综合评级（带横向条形图）
- **K线走势图**：近120日收盘价折线图 + 成交量柱状图（Chart.js 交互式图表）
- **可视化图表**：技术面价格区间图、资金面现金流横向条、新闻情感堆叠条
- **财务数据表**：营收/净利润/ROE/EPS/现金流
- **全网舆情**：各平台帖子列表（标注来源/点赞/评论，含微信公众号文章链接）
- **机构研报**：评级分布、EPS预测、PDF下载链接
- **AI 综合分析报告**：Markdown 渲染为排版良好的 HTML，含公司概况、财务评估、情绪分析、技术简析、投资建议、风险提示
- **数据来源清单**：每个平台采集状态

报告为纯静态 HTML，可直接通过浏览器打开或分享。

---

## 免责声明

本报告由 AI 自动生成，所有分析内容仅供参考，不构成任何投资建议。投资有风险，入市需谨慎。
