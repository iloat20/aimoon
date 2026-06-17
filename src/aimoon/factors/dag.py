"""因子依赖图 (DAG) — AST 静态分析构建因子计算依赖关系。

通过分析每个因子 compute() 函数的 AST，提取:
1. 对 panel key 的访问 → 原始列依赖
2. base 算子调用 → 滚动窗口大小
3. 因子间依赖 (目前无，但预留接口)

用于增量更新: 新 K 线到达时，仅重算依赖该列的因子。
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aimoon.factors.registry import Registry

logger = logging.getLogger(__name__)

# base 算子中涉及滚动窗口的函数
_ROLLING_OPS = frozenset(
    {
        "ts_mean",
        "ts_std",
        "ts_max",
        "ts_min",
        "ts_rank",
        "ts_corr",
        "ts_cov",
        "ts_argmax",
        "ts_argmin",
        "delta",
        "decay_linear",
    }
)

# panel 中可能的原始列
_RAW_COLUMNS = frozenset(
    {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    }
)

# panel 中可能的派生列 (由 add_all_indicators_batch 注入)
_DERIVED_COLUMNS = frozenset(
    {
        "ma5",
        "ma10",
        "ma20",
        "panel_ma60",
        "rsi14",
        "macd_dif",
        "macd_dea",
        "macd_hist",
        "kdj_k",
        "kdj_d",
        "kdj_j",
        "boll_upper",
        "boll_mid",
        "boll_lower",
        "vol_ratio",
        "returns",
        "vwap",
    }
)


@dataclass(frozen=True)
class DAGNode:
    """因子依赖图节点。"""

    id: str
    node_type: str  # "raw_col" | "derived_col" | "factor"
    deps: tuple[str, ...] = ()  # 依赖的节点 ID
    col_refs: tuple[str, ...] = ()  # 引用的原始列名
    max_window: int = 0  # 最大滚动窗口
    is_cross_sectional: bool = False  # 是否包含截面算子 (rank/scale)


@dataclass
class FactorDAG:
    """因子依赖图。"""

    nodes: dict[str, DAGNode] = field(default_factory=dict)
    # col -> 依赖该列的因子 ID 列表
    col_to_factors: dict[str, list[str]] = field(default_factory=dict)
    # 因子按窗口大小分组
    factors_by_window: dict[int, list[str]] = field(default_factory=dict)


def build_factor_dag(registry: Registry) -> FactorDAG:
    """从注册表 AST 扫描构建因子 DAG。

    Parameters
    ----------
    registry : Registry
        因子注册表实例。

    Returns
    -------
    FactorDAG
        因子依赖图。
    """
    dag = FactorDAG()

    # 添加原始列节点
    for col in _RAW_COLUMNS:
        dag.nodes[col] = DAGNode(id=col, node_type="raw_col")

    for alpha_id in registry.list():
        py_path = registry._py_paths.get(alpha_id)
        if py_path is None:
            continue

        node = _analyze_factor(alpha_id, py_path)
        dag.nodes[alpha_id] = node

        # 建立列 -> 因子映射
        for col_ref in node.col_refs:
            dag.col_to_factors.setdefault(col_ref, []).append(alpha_id)

        # 按窗口分组
        dag.factors_by_window.setdefault(node.max_window, []).append(alpha_id)

    logger.info(
        "Factor DAG built: %d factors, %d columns, %d window groups",
        len(registry.list()),
        len(dag.col_to_factors),
        len(dag.factors_by_window),
    )
    return dag


def _analyze_factor(alpha_id: str, py_path: Path) -> DAGNode:
    """AST 分析单个因子的依赖。"""
    try:
        source = py_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        logger.debug("AST parse failed for %s: %s", alpha_id, exc)
        return DAGNode(id=alpha_id, node_type="factor")

    col_refs: set[str] = set()
    windows: list[int] = []
    has_cross_sectional = False

    for node in ast.walk(tree):
        # panel["close"] / panel.get("close")
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name) and node.value.id == "panel":
                if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    col_refs.add(node.slice.value)
            elif isinstance(node.value, ast.Call):
                func = node.value.func
                if isinstance(func, ast.Attribute) and func.attr == "get":
                    if isinstance(func.value, ast.Name) and func.value.id == "panel":
                        if node.value.args:
                            arg = node.value.args[0]
                            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                                col_refs.add(arg.value)

        # ts_xxx(..., n) / delta(..., n) 调用
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fname = node.func.id
            if fname in _ROLLING_OPS and len(node.args) >= 2:
                last_arg = node.args[-1]
                if isinstance(last_arg, ast.Constant) and isinstance(last_arg.value, (int, float)):
                    windows.append(int(last_arg.value))

        # rank() / scale() 截面算子
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ("rank", "scale"):
                has_cross_sectional = True

    # 确定 max_window (保守估计)
    max_window = max(windows) if windows else 0

    # col_refs 过滤: 只保留已知列
    valid_col_refs = col_refs & (_RAW_COLUMNS | _DERIVED_COLUMNS)

    return DAGNode(
        id=alpha_id,
        node_type="factor",
        col_refs=tuple(sorted(valid_col_refs)),
        max_window=max_window,
        is_cross_sectional=has_cross_sectional,
    )


def get_affected_factors(
    dag: FactorDAG,
    changed_columns: set[str],
) -> tuple[list[str], list[str]]:
    """给定变化的列，返回受影响的因子 ID。

    Parameters
    ----------
    dag : FactorDAG
        因子依赖图。
    changed_columns : set[str]
        变化的原始列名 (如 {"close", "volume"})。

    Returns
    -------
    tuple[list[str], list[str]]
        (incremental_factors, cross_sectional_factors)
        - incremental_factors: 可增量更新的因子 (仅时序依赖)
        - cross_sectional_factors: 需要批量更新的因子 (有截面算子)
    """
    affected: set[str] = set()

    for col in changed_columns:
        for factor_id in dag.col_to_factors.get(col, []):
            affected.add(factor_id)

    incremental: list[str] = []
    cross_sectional: list[str] = []

    for factor_id in sorted(affected):
        node = dag.nodes.get(factor_id)
        if node is None:
            continue
        if node.is_cross_sectional:
            cross_sectional.append(factor_id)
        else:
            incremental.append(factor_id)

    return incremental, cross_sectional
