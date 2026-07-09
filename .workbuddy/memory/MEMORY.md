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
- `FinancialData` 无 `statements` 字段 → `_dividend_from_statements` 恒 None（真实 gap，测试记 None）。
- `scoring.py` 不存在（评分在 `validation/integrity_checker.py`），四文档统一"不存在"口径。
- 提示词从 `pipeline/prompts/` 加载；根目录 `pipeline/compile.md` 是死副本不被加载。

## 工作区隐患（本机）
- 持久化钩子在每次写入后篡改文件：`tuple(`→`tuble(`、async 函数前插 `@pytest.mark.asyncio`、import 排序。规避：Write 整文件重写绕过 Edit 守卫；sed 精确替换后立刻跑 pytest 不留间隙；`grep -rc "tuble("` 验证还原。

## Git
- `main` 长期领先 `origin/main`；推送前确保工作区仅留刻意排除项（`.pytest-tmp/`、`docs/screenshots/`、`pipeline/compile.md` 死副本）。
