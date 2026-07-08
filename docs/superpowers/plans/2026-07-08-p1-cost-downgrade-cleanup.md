# P1: COMPILE 成本下调 + 瘦身整理 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地审计 P1 中仍可推进的部分 — 把 COMPILE 阶段 reasoning effort 从 `high` 降到 `medium`(A),并清理 peer_compare 双实现(B.1)、文档不同步(B.2)和 ~118 行死代码(B.3)。独立自检阶段(`_phase_self_check`)保留不动(尊重 commit `4637ef73` 的近期设计)。

**Architecture:** 七块工作量按 A → B.1 → B.2 → B.3(a+b) → B.3(c) → B.3(d) → B.3(e) 顺序各自独立可测试可提交。A 只动 orchestrator `_phase_compile` 单处 effort 常量 + docstring;B.1 让 orchestrator `_run_peer_compare` 委托 `peer_compare.run`,保留 `{peers, industry}` 返回形状;B.2 纯文档字符串替换,`grep` 验收;B.3 删除 grep 证实的死代码("grep-0-ref → rm → pytest 全绿"循环)。

**Tech Stack:** Python 3.12 / ruff / mypy / pytest / aimoon pipeline v2 orchestrator / akshare / peer_compare `tool_safe` 装饰器

---

## 范围与验证背景(来自 spec,实施时按此核查,不凭审计猜测)

改动文件清单:
- A 改:`src/aimoon/adapters/driven/ai/pipeline/orchestrator.py:401-447`(仅 `_phase_compile` 主调用 1 处 effort + docstring)
- A 不改(保留):`_phase_self_check` / `_parse_self_check_json` / SELF_CHECK enum / `test_pipeline_phases.py` 自检测验 / ANALYSIS effort / 模式矩阵
- B.1 BUG-4 改:`orchestrator.py:716-733`(`_run_peer_compare`);依赖 `tools/peer_compare.py:87-116`(`run(name, self_fin, search_fn)`,@tool_safe 兜底);保 `render_peer_comparison` 消费 `data.get("peers")`(`table_renderer.py:49`,orchestrator L259)
- B.2 文档 7 处(grep 验证 0 引用):CLAUDE.md L7 版本号、CLAUDE.md L127 Tencent 单位、ARCHITECTURE.md L149 xueqiu.py、ARCHITECTURE.md L166 pysnowball_adapter、ARCHITECTURE.md L167 annual_report.py、README.md 补 --fast、AGENTS.md scoring.py 统一口径
- B.3 死代码 5 类(grep → 删 → pytest 全绿):
  - a `pipeline/prompts/__init__.py` 整个文件(12 行,与 phases.py 双 defs 中的死者,orchestrator 从 phases 进口)
  - b `pipeline/section_coverage.py` 整个文件 + `tests/test_section_coverage.py`(配套删)
  - c `analyzer.py:80-94` `_build_fallback_report` 模块函数
  - d `main.py:32-43` `_suppress_asyncio_pipe_warning` 函数 + L54 单次 calling
  - e `akshare_adapter.py:244-269` `_sync_income/_sync_balance/_sync_cashflow` 三个死方法(grep 已确认 0 引用)

---

## 工作流公约(所有 Task 共用,不重复写)

每次改动后三连验证:
```bash
uv run ruff check src/         # 必须干净
uv run mypy src/aimoon/        # 提交前 0 error
uv run pytest -m "not integration" -q   # 必须全绿
```

git commit messages 用 `feat:` / `fix:` / `refactor:` / `docs:` 小 commit,信息包含文件名与简短描述。

---

## Task 1: COMPILE `reasoning_effort` high → medium

**Files:**
- Test:`tests/test_pipeline_phases.py`(已 pass,改后仍 pass——自检不动)
- Modify:`src/aimoon/adapters/driven/ai/pipeline/orchestrator.py:399-447`(`_phase_compile`)

- [ ] **Step 1: 跑当前测验基线**

```bash
uv run ruff check src/aimoon/adapters/driven/ai/pipeline/orchestrator.py
uv run mypy src/aimoon/ 2>&1 | tail -5
uv run pytest -m "not integration" tests/test_pipeline_phases.py -q 2>&1 | tail -5
```

Expected:ruff 干净 / mypy 0 error / `test_pipeline_phases.py` 全绿。

- [ ] **Step 2: 改 effort 常量(L418)**

在 `src/aimoon/adapters/driven/ai/pipeline/orchestrator.py` 的 `_phase_compile` 内找到:
```python
                    text = await self._stream_llm_content(messages, reasoning_effort="high")
```
改为:
```python
                    text = await self._stream_llm_content(messages, reasoning_effort="medium")
```

- [ ] **Step 3: 改 docstring(L399-404)**

把 `_phase_compile` 当前 docstring 首段从:
```
        使用流式调用(reasoning_effort=high)在长文生成时持续打印章节进度;
        reasoning 由 "max" 降为 "high" 以显著降低 300s 超时导致整篇 partial
        降级的概率(ANALYSIS 阶段已做过深度推理,此处无需再次 "max")。
```
改为:
```
        使用流式调用(reasoning_effort=medium)在长文生成时持续打印章节进度;
        ANALYSIS 阶段已完成深度推理,终稿阶段只做格式化/扩写,无需再次深邃推理,
        把 reasoning 由 "high" 降为 "medium" 以节省 COMPILE 阶段 ~50-60% reasoning tokens,
        同时降低 300s 超时导致整篇 partial 降级的概率。
```

- [ ] **Step 4: 三连验证**

```bash
uv run ruff check src/aimoon/adapters/driven/ai/pipeline/orchestrator.py
uv run mypy src/aimoon/ 2>&1 | tail -3
uv run pytest -m "not integration" tests/test_pipeline_phases.py -q 2>&1 | tail -3
```

Expected:ruff 干净 / mypy 0 / 测验仍绿(自检测验 3-phase 枚举 / 4 variant JSON / 2 integration 全绿,证明没动自检)。

- [ ] **Step 5: Commit**

```bash
git add src/aimoon/adapters/driven/ai/pipeline/orchestrator.py
git commit -m "perf(pipeline): downgrade COMPILE reasoning_effort high→medium

终稿格式化/扩写阶段,ANALYSIS 已完成深度推理,无需高阶 reasoning。
COMPILE 占整管线 reasoning ~60%,此改动省 ~30% 整管线 reasoning tokens。
独立自检阶段保留不动;design 仅取 effort 分支,不触碰自检。

Refs: docs/superpowers/specs/2026-07-08-p1-design.md"
```

---

## Task 2 (B.1): BUG-4 单源 peer_compare

**Files:**
- Modify:`src/aimoon/adapters/driven/ai/pipeline/orchestrator.py:716-733`(`_run_peer_compare`)
- Dep (保留不变):`src/aimoon/adapters/driven/ai/tools/peer_compare.py:87-116`
- Test:`tests/test_tools_peer_compare.py`,`tests/test_orchestrator_wiring.py`

- [ ] **Step 1: 前置测验 + grep 确认返回契约**

```bash
uv run pytest -m "not integration" tests/test_tools_peer_compare.py tests/test_orchestrator_wiring.py -q 2>&1 | tail -5
grep -n "__partial__\|\"peers\"" src/aimoon/adapters/driven/ai/pipeline/orchestrator.py | sed -n '1,12p'
```

Expected:测验全绿;orchestrator L728 `{"__partial__": "no_data", "peers": []}`、L727/L731-732 `render_peer_comparison` 消费 `data.get("peers")`(参见 `table_renderer.py:49`)。

- [ ] **Step 2: 替换 `_run_peer_compare` 为委托 `peer_compare.run`**

在 `orchestrator.py` 把 `_run_peer_compare` 重写为(单源,orchestration 层只编排):
```python
async def _run_peer_compare(si: object, search_fn) -> dict:
    """委托 ``peer_compare.run`` 单一入口,保持 ``{peers, industry}`` 返回形状。

    ``search_fn`` 直接透传给工具,避免 orchestrator 层进入
    ``build_search_query``/``parse`` 的实现细节;
    所有未预期错误由 ``@tool_safe`` 兜底为 ``{"__partial__": "no_data"}``。
    """
    from ..tools.peer_compare import run as peer_run

    name = str(getattr(si, "name", "") or getattr(si, "symbol", "") or "")
    self_fin = getattr(si, "financial", None)
    return await peer_run(name=name, self_fin=self_fin, search_fn=search_fn)
```

`peer_compare.run` 签名 `def run(name: str, self_fin: FinancialData | None, search_fn: Callable | None = None) -> dict`(Step 1 已 grep)。原代码里 `from ..tools.peer_compare import build_search_query` 与 `parse as peer_parse` 两行在替换后不再需要,一并删除。

- [ ] **Step 3: 三连验证**

```bash
uv run ruff check src/aimoon/adapters/driven/ai/pipeline/orchestrator.py
uv run mypy src/aimoon/ 2>&1 | tail -3
uv run pytest -m "not integration" tests/test_tools_peer_compare.py tests/test_orchestrator_wiring.py -q 2>&1 | tail -3
```

Expected:绿;`test_orchestrator_wiring.py` 是回归触发验证点(原审计 BUG-4 位置)。

- [ ] **Step 4: 补测验防回归(可选 TDD 强任务)**

在 `tests/test_tools_peer_compare.py` 加一个 case 验 search_fn 返空时返回形仍带 `peers`:
```python
@pytest.mark.unit
def test_run_no_html_returns_peers_shape():
    async def _run(html: str) -> dict:
        async def _search(_q: str) -> str:
            return html
        from aimoon.adapters.driven.ai.tools import peer_compare
        return await peer_compare.run(name="贵州茅台", self_fin=None, search_fn=_search)

    out = asyncio.run(_run(""))
    assert isinstance(out.get("peers"), list)
    assert "industry" in out
```
若加此 step,"先写测试(RED) → 跑 Step 3 验通过(GREEN)";但当前实现已满足,直接 GREEN 也可,用 `pytest -q` 判定。

- [ ] **Step 5: Commit**

```bash
git add src/aimoon/adapters/driven/ai/pipeline/orchestrator.py
git add tests/test_tools_peer_compare.py 2>/dev/null || true
git commit -m "refactor(pipeline): delegate _run_peer_compare to peer_compare.run

orchestrator 层不再直接 import build_search_query/parse,
与 peer_compare.run 单源,错误走 @tool_safe 兜底。
保持 render_peer_comparison 消费的 {peers, industry} 返回形状不变。

Fixes: docs/superpowers/specs/2026-07-08-p1-design.md (BUG-4)"
```

---

## Task 3 (B.2): BUG-5 文档同步

**Files:** `docs/` 无自动验证,用 grep 验收
**Modify (7 处 exact edit):**
- `CLAUDE.md` L7、L127
- `ARCHITECTURE.md`(根目录)L149、L166、L167
- `README.md`(根目录)补 --fast
- `AGENTS.md` scoring 口径统一(不删)

- [ ] **Step 1: 读当前行确认要改的字符串**

```bash
sed -n '7p;127p' CLAUDE.md
grep -n "scoring.py" AGENTS.md
```

Expected:确认 CLAUDE.md L7 含 `version 0.4.0`、L127 含 `Tencent volume unit = 手 × 100`、AGENTS.md 多处已写「scoring.py 并不存在」。spec 决定把不一致那处改成相同口径,不删条目。

- [ ] **Step 2: #1 CLAUDE.md L7 版本 0.4.0 → 0.4.2**

当前:`version 0.4.0.`
Edit:`version 0.4.0.` → `version 0.4.2.`

- [ ] **Step 3: #2 CLAUDE.md L127 Tencent 单位**

当前:`- **K-line 3-tier** — akshare ... Tencent volume unit = 手 × 100`
Edit:`Tencent volume unit = 手 × 100` → `Tencent volume unit = 手(腾讯接口直接返回手,无需 ×100)`

- [ ] **Step 4: #3 ARCHITECTURE.md root L149 删除 `xueqiu.py`**

当前(L149):`` `quote.py`、`kline.py`、`capital_flow.py`、`xueqiu.py` 等 — 各维度采集器``
Edit:删除 ``、`xueqiu.py` ``,保留其余。

- [ ] **Step 5: #4 ARCHITECTURE.md root L166 pysnowball → akshare**

当前(L166):`` `pysnowball_adapter.py` — 雪球财务数据``
Edit:→ `` `akshare_adapter.py` — akshare(东方财富)财务数据``(`pysnowball` 已在 commit `2b5ce349` 移除)。

- [ ] **Step 6: #5 ARCHITECTURE.md root L167 删除 `annual_report.py` 行**

当前(L167):`` `annual_report.py` — 年报适配器 ``(单独一行)
Edit:删除整行(`financial/` 块上下文仍在)。

- [ ] **Step 7: #6 README.md 补 `--fast` CLI**

在 README.md 命令行段(`aimoon 000858 -o ./reports` 行)后追加两行:
```
aimoon 600519 --fast           # 快速模式(跳过自检+COMPILE,直接出初稿)
aimoon 600519 --mock --fast    # Mock + 快速组合
```

- [ ] **Step 8: #7 AGENTS.md scoring.py 统一口径**

仅把不一致的那处(若有)改成与 CLAUDE.md / ARCHITECTURE.md「scoring.py 实际不存在」相同口径。不要删除条目,保持现有「未落地」注释。

- [ ] **Step 9: grep 0 引用验收 + 跑代码测验**

```bash
grep -rni "xueqiu\.py\|pysnowball_adapter\|annual_report\.py" CLAUDE.md AGENTS.md ARCHITECTURE.md README.md docs/ 2>/dev/null | grep -v "并不存在\|未落地\|评分模块并不存在\|该模块并不存在"
uv run ruff check src/
uv run pytest -m "not integration" -q 2>&1 | tail -3
```

Expected:grep 0 命中 / 测验仍 151 passed。

- [ ] **Step 10: Commit**

```bash
git add CLAUDE.md ARCHITECTURE.md README.md AGENTS.md
git commit -m "docs: fix version/Tencent-unit/cli-flag and dead filenames in architecture docs

- CLAUDE.md 版本号 0.4.0→0.4.2
- CLAUDE.md Tencent 单位「手 × 100」→「手」
- ARCHITECTURE.md 删除已不存在的 xueqiu.py / annual_report.py 条目,
  pysnowball_adapter 改为 akshare_adapter(pysnowball 已移除)
- README.md 补 --fast CLI 说明
- AGENTS.md scoring.py 口径统一「未落地」

Verification:grep docs/ 死文件名 → 0 hits。

Fixes: docs/superpowers/specs/2026-07-08-p1-design.md (BUG-5)"
```

---

## Task 4 (B.3-a,b): 删死代码 `prompts/__init__.py` + `section_coverage.py`

**Files:**
- Delete:`src/aimoon/adapters/driven/ai/pipeline/prompts/__init__.py`(orchestrator 进口 phases 版本)
- Delete:`src/aimoon/adapters/driven/ai/pipeline/section_coverage.py` + `tests/test_section_coverage.py`

- [ ] **Step 1: grep 验 a 0 ref**

```bash
grep -rn "pipeline.prompts import\|pipeline\.prompts\b" src/ tests/ 2>/dev/null | grep -v pyc | grep -v _SECTIONS_MD
```

Expected:0 命中。

- [ ] **Step 2: 删 a**

```bash
rm src/aimoon/adapters/driven/ai/pipeline/prompts/__init__.py
python -c "from aimoon.adapters.driven.ai.pipeline import orchestrator, phases; print('ok')"
```

Expected:`ok`(orchestrator 从 phases 进口,不需要 prompts.__init__ loader)。

- [ ] **Step 3: grep 验 b section_coverage.py 0 ref**

```bash
grep -rn "section_coverage" src/ 2>/dev/null | grep -v pyc
```

Expected:仅 `tests/test_section_coverage.py`。

- [ ] **Step 4: 删 b + 配套测验**

```bash
rm src/aimoon/adapters/driven/ai/pipeline/section_coverage.py
rm tests/test_section_coverage.py
uv run ruff check src/
uv run pytest -m "not integration" -q 2>&1 | tail -3
```

Expected:ruff 干净 / 测验仍绿。

- [ ] **Step 5: Commit**

```bash
git add -u
git status --short
git commit -m "refactor: remove dead prompts/__init__.py + section_coverage.py(+配套测验)

orchestrator 已 import from phases.py,不需要 prompts.__init__ loader;
section_coverage.py 仅被它自己的测验调用,grep 0 ref 全删;
删配套测试以免孤儿。"
```

---

## Task 5 (B.3-c): 删死代码 `_build_fallback_report`

**Files:**
- Delete:`src/aimoon/adapters/driven/ai/analyzer.py` L80-94(仅函数体,保留 `StockAnalysis` import)

- [ ] **Step 1: grep 验 c 0 ref**

```bash
grep -rn "_build_fallback_report\|fallback_report" src/ tests/ 2>/dev/null | grep -v pyc
```

Expected:仅 `analyzer.py:80:def _build_fallback_report`(定义自身,0 调用者)。

- [ ] **Step 2: 删 L80-94**

在 `analyzer.py` 删除整个 `_build_fallback_report` 函数体(含 docstring 与函数内 `import` 片段,如有),**但**保留文件顶 `StockAnalysis` import——其它函数仍用。

- [ ] **Step 3: 三连验证**

```bash
uv run ruff check src/aimoon/adapters/driven/ai/analyzer.py
uv run mypy src/aimoon/ 2>&1 | tail -3
uv run pytest -m "not integration" -q 2>&1 | tail -3
```

Expected:绿。

- [ ] **Step 4: Commit**

```bash
git add src/aimoon/adapters/driven/ai/analyzer.py
git commit -m "refactor(analyzer): remove dead _build_fallback_report helper

grep 0 调用者;StockAnalysis import 保留(其它函数在用)。"
```

---

## Task 6 (B.3-d): 删死代码 `_suppress_asyncio_pipe_warning`

**Files:**
- Delete:`src/aimoon/adapters/driving/cli/main.py` L32-43(函数)+ L54 单次 calling

- [ ] **Step 1: grep 验 d 仅 main.py 自引用**

```bash
grep -rn "_suppress_asyncio_pipe_warning\|suppress_asyncio" src/ | grep -v pyc
```

Expected:`main.py:32:def` + `main.py:54: _suppress_asyncio_pipe_warning()`(调用)。

- [ ] **Step 2: 删函数 + calling**

在 `main.py` 删除 `_suppress_asyncio_pipe_warning` 函数(含 docstring),再删 `main()` 内对该函数的调用行(位于 warnings 配置之后)。

- [ ] **Step 3: 三连验证**

```bash
uv run ruff check src/aimoon/adapters/driving/cli/main.py
uv run mypy src/aimoon/ 2>&1 | tail -3
uv run pytest -m "not integration" -q 2>&1 | tail -3
```

Expected:绿。

- [ ] **Step 4: Commit**

```bash
git add src/aimoon/adapters/driving/cli/main.py
git commit -m "refactor(main.py): remove dead _suppress_asyncio_pipe_warning helper

与模块级 L22-23(os.environ + logging.getLogger)直接重复;
grep 仅 main.py 自引用。"
```

---

## Task 7 (B.3-e): 删死代码 akshare 三个 `_sync_*` 方法

**Files:**
- Delete:`src/aimoon/adapters/driven/financial/akshare_adapter.py` `_sync_income` / `_sync_balance` / `_sync_cashflow` 三个方法

- [ ] **Step 1: grep 验 e 0 ref**

```bash
grep -rn "_sync_income\|_sync_balance\|_sync_cashflow" src/ tests/ | grep -v pyc
```

Expected:仅定义处(三大方法 def 行,无调用者)。

- [ ] **Step 2: 删三个方法**

在 `akshare_adapter.py` 删除以下三块(每块含函数体内 `import akshare as ak`,该 import 只在此函数用,一并删):
- `_sync_income` 方法整块
- `_sync_balance` 方法整块
- `_sync_cashflow` 方法整块

保留模块顶 `import akshare as ak`(L16)——`_fetch_triple` 等其它函数在用。
如三个方法之间/前后存在空行冗余,ruff 会自动修,也可手拾。

- [ ] **Step 3: 三连验证**

```bash
uv run ruff check src/aimoon/adapters/driven/financial/akshare_adapter.py
uv run mypy src/aimoon/ 2>&1 | tail -3
uv run pytest -m "not integration" -q 2>&1 | tail -3
```

Expected:绿。

- [ ] **Step 4: Commit**

```bash
git add src/aimoon/adapters/driven/financial/akshare_adapter.py
git commit -m "refactor(akshare): remove dead _sync_income/_sync_balance/_sync_cashflow

三大同步方法均为 _fetch_triple 并行前遗留,grep 0 引用;
模块顶 import akshare as ak 保留(parallel path 在用)。

Refs: docs/superpowers/specs/2026-07-08-p1-design.md"
```
```
