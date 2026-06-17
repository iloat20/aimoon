# CLI Main 函数重构计划

**目标**: 将 main() 函数从 241 行拆分为多个更小的函数

**原则**:
- 每个函数不超过 50 行
- 每个函数只负责一个功能
- 提高可读性和可测试性

## 重构方案

### 1. 提取命令处理函数

将每个命令的处理逻辑提取为独立函数：

```python
def _handle_cache_command(cfg: Config) -> None:
    """处理缓存管理命令"""
    cache = DataCache(cfg.cache_dir, cfg.cache_ttl_hours)
    print(f"Cleared {cache.clear()} cached files")

def _handle_watchlist_command(args: argparse.Namespace, fmt: OutputFormatter) -> None:
    """处理自选股票命令"""
    # 原有逻辑...

def _handle_update_command(cfg: Config) -> None:
    """处理更新命令"""
    # 原有逻辑...

def _handle_refresh_pool_command(cfg: Config, fmt: OutputFormatter) -> None:
    """处理刷新持仓池命令"""
    # 原有逻辑...

def _handle_train_model_command(cfg: Config, fmt: OutputFormatter) -> None:
    """处理训练模型命令"""
    # 原有逻辑...

def _handle_screening_command(args: argparse.Namespace, cfg: Config, fmt: OutputFormatter) -> None:
    """处理筛选命令"""
    # 原有逻辑...
```

### 2. 重构后的 main() 函数

```python
def main() -> None:
    """CLI 入口点"""
    args = parse_args()
    cfg = load_config(args, path=getattr(args, "config", None))
    fmt = OutputFormatter(cfg)

    # 命令路由
    command_handlers = {
        "cache": lambda: _handle_cache_command(cfg),
        "watchlist": lambda: _handle_watchlist_command(args, fmt),
        "update": lambda: _handle_update_command(cfg),
        "refresh-pool": lambda: _handle_refresh_pool_command(cfg, fmt),
        "train-model": lambda: _handle_train_model_command(cfg, fmt),
        "evaluate": lambda: _run_evaluate(args, cfg, fmt),
        "backtest": lambda: _run_backtest(args, cfg, fmt),
        "optimize": lambda: _run_optimize(args, cfg, fmt),
        "schedule": lambda: _run_schedule(args, cfg, fmt),
    }

    # 执行对应命令或默认筛选
    handler = command_handlers.get(cfg.command)
    if handler:
        handler()
    else:
        _handle_screening_command(args, cfg, fmt)
```

### 3. 实施步骤

1. 创建新的命令处理函数
2. 提取 main() 中的逻辑到各个函数
3. 简化 main() 为命令路由
4. 测试确保功能正常

### 4. 预期结果

- main() 函数: 241 行 → ~30 行
- 每个命令处理函数: 30-50 行
- 代码更易读、易测试、易维护
