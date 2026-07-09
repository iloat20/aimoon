# 可观测性优化设计

**日期**：2026-06-30
**目标**：增加日志和性能指标，便于排查问题和监控性能

## 背景

当前系统缺乏性能监控和详细的日志，出现问题时难以定位。

## 方案

### 1. 采集器耗时日志

在 `base.py` 的 `DataCollector` 基类中添加耗时记录：

```python
async def fetch(self, symbol: str, **kwargs: Any) -> T:
    start = time.monotonic()
    try:
        result = await self._fetch_uncached(symbol, **kwargs)
        elapsed = int((time.monotonic() - start) * 1000)
        logger.info("[%s] completed in %dms", self.name, elapsed)
        return result
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        logger.warning("[%s] failed in %dms: %s", self.name, elapsed, e)
        raise
```

### 2. AI 分析性能日志

在 `analyzer.py` 的 `analyze` 方法中添加性能记录：

```python
import time

start = time.monotonic()
# ... existing logic ...
elapsed = int((time.monotonic() - start) * 1000)
logger.info("[ai_analysis] completed in %dms, output %d chars", elapsed, len(md))
```

### 3. Pipeline 总耗时

在 `stock_analysis_service.py` 的 `collect_and_analyze` 函数中添加总耗时记录：

```python
import time

start = time.monotonic()
# ... existing logic ...
elapsed = int((time.monotonic() - start) * 1000)
logger.info("[pipeline] total: %dms", elapsed)
```

## 修改文件

1. `src/aimoon/adapters/driven/collectors/base.py` — 采集器耗时日志
2. `src/aimoon/adapters/driven/ai/analyzer.py` — AI 分析性能日志
3. `src/aimoon/core/application/services/stock_analysis_service.py` — Pipeline 总耗时

## 不修改

- 数据采集逻辑
- AI 分析逻辑
- 报告生成逻辑
- 缓存机制
