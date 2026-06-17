"""Alpha 因子注册表 — AST 扫描 zoo 模块，验证元数据，惰性导入计算。

移植自 HKUDS/Vibe-Trading (MIT)，简化为 frozen dataclass（无 pydantic 依赖）。
每个因子文件包含 __alpha_meta__ 字典字面量 + compute(panel) 函数。
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import logging
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_MAX_PY_BYTES = 200_000


@dataclass(frozen=True)
class AlphaMeta:
    """因子元数据 — 从 __alpha_meta__ 字典字面量提取。"""

    id: str
    nickname: str | None = None
    theme: list[str] = field(default_factory=list)
    formula_latex: str = ""
    columns_required: list[str] = field(default_factory=list)
    extras_required: list[str] = field(default_factory=list)
    requires_sector: bool = False
    universe: list[str] = field(default_factory=list)
    frequency: list[str] = field(default_factory=lambda: ["1d"])
    decay_horizon: int = 5
    min_warmup_bars: int = 0
    notes: str = ""


@dataclass(frozen=True, slots=True)
class Alpha:
    """注册表持有的因子句柄。"""

    id: str
    zoo: str
    module_path: str
    meta: dict[str, Any] = field(default_factory=dict)


class SkipAlphaError(Exception):
    """当因子的前置条件（列、板块）不满足时抛出。"""


class RegistryError(Exception):
    """注册表级配置错误。"""


@dataclass(frozen=True, slots=True)
class _LoadError:
    alpha_id: str
    reason: str


def _validate_id_token(token: str, kind: str) -> None:
    if not _ID_RE.fullmatch(token):
        raise RegistryError(f"invalid {kind} {token!r}: must match {_ID_RE.pattern}")


def load_alpha_meta_from_py(path: Path) -> AlphaMeta:
    """AST 提取 __alpha_meta__ 字典字面量。不执行导入。"""
    size = path.stat().st_size
    if size > _MAX_PY_BYTES:
        raise RegistryError(f"{path.name}: {size}B exceeds {_MAX_PY_BYTES}B cap")

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    meta_node: ast.expr | None = None
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        targets = [t for t in stmt.targets if isinstance(t, ast.Name)]
        if any(t.id == "__alpha_meta__" for t in targets):
            meta_node = stmt.value
            break

    if meta_node is None:
        raise RegistryError(f"{path.name}: __alpha_meta__ assignment not found")

    try:
        raw = ast.literal_eval(meta_node)
    except (ValueError, SyntaxError) as exc:
        raise RegistryError(f"{path.name}: __alpha_meta__ not a literal: {exc}") from exc

    if not isinstance(raw, dict):
        raise RegistryError(f"{path.name}: __alpha_meta__ must be dict, got {type(raw).__name__}")

    return AlphaMeta(**raw)


def _zoo_dir_default() -> Path:
    return Path(__file__).parent / "zoo"


# Registry cache file
_REGISTRY_CACHE_FILE = Path(__file__).parent / ".registry_cache.json"


def _compute_zoo_fingerprint(zoo_root: Path) -> str:
    """Compute a fingerprint of the zoo directory based on file mtimes and sizes."""
    import hashlib

    hasher = hashlib.md5()
    for py_file in sorted(zoo_root.rglob("*.py")):
        if py_file.name.startswith("_"):
            continue
        stat = py_file.stat()
        hasher.update(f"{py_file.relative_to(zoo_root)}:{stat.st_mtime_ns}:{stat.st_size}".encode())
    return hasher.hexdigest()


def _load_registry_cache(zoo_root: Path) -> tuple[dict, dict, list] | None:
    """Load registry from cache if fingerprint matches."""
    if not _REGISTRY_CACHE_FILE.exists():
        return None
    try:
        with open(_REGISTRY_CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
        # Verify fingerprint
        current_fp = _compute_zoo_fingerprint(zoo_root)
        if cache.get("fingerprint") != current_fp:
            return None
        # Restore alphas, py_paths, load_errors
        alphas = {}
        for aid, data in cache.get("alphas", {}).items():
            alphas[aid] = Alpha(
                id=data["id"],
                zoo=data["zoo"],
                module_path=data["module_path"],
                meta=data["meta"],
            )
        py_paths = {aid: Path(p) for aid, p in cache.get("py_paths", {}).items()}
        load_errors = [_LoadError(e["alpha_id"], e["reason"]) for e in cache.get("load_errors", [])]
        return alphas, py_paths, load_errors
    except (json.JSONDecodeError, KeyError, TypeError, FileNotFoundError):
        return None


def _save_registry_cache(zoo_root: Path, alphas: dict, py_paths: dict, load_errors: list) -> None:
    """Save registry to cache."""
    try:
        fingerprint = _compute_zoo_fingerprint(zoo_root)
        cache = {
            "fingerprint": fingerprint,
            "alphas": {
                aid: {
                    "id": a.id,
                    "zoo": a.zoo,
                    "module_path": a.module_path,
                    "meta": a.meta,
                }
                for aid, a in alphas.items()
            },
            "py_paths": {aid: str(p) for aid, p in py_paths.items()},
            "load_errors": [{"alpha_id": e.alpha_id, "reason": e.reason} for e in load_errors],
        }
        with open(_REGISTRY_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except (OSError, TypeError):
        pass


_nan_warned: set = set()  # Module-level mutable set (not part of frozen dataclass)
_module_load_lock = threading.Lock()


class Registry:
    """内存中的因子注册表，扫描 zoo 子目录。"""

    def __init__(self, zoo_root: Path | None = None) -> None:
        default_root = _zoo_dir_default()
        self._zoo_root = (zoo_root or default_root).resolve()
        self._use_filesystem_loader = self._zoo_root != default_root.resolve()
        self._py_paths: dict[str, Path] = {}
        self._alphas: dict[str, Alpha] = {}
        self._load_errors: list[_LoadError] = []

        # Try to load from cache first
        cached = _load_registry_cache(self._zoo_root)
        if cached is not None:
            self._alphas, self._py_paths, self._load_errors = cached
            logger.info("Registry loaded from cache: %d factors", len(self._alphas))
        else:
            self._scan()
            _save_registry_cache(self._zoo_root, self._alphas, self._py_paths, self._load_errors)

    def _scan(self) -> None:
        if not self._zoo_root.is_dir():
            return
        for zoo_dir in sorted(self._zoo_root.iterdir()):
            if not zoo_dir.is_dir():
                continue
            zoo_id = zoo_dir.name
            if zoo_id.startswith("_") or zoo_id == "__pycache__":
                continue
            try:
                _validate_id_token(zoo_id, "zoo_id")
            except RegistryError as exc:
                self._load_errors.append(_LoadError(zoo_id, str(exc)))
                continue
            for py_file in sorted(zoo_dir.glob("*.py")):
                if py_file.name.startswith("_"):
                    continue
                self._try_register(zoo_id, py_file)

    def _try_register(self, zoo_id: str, py_file: Path) -> None:
        short_id = py_file.stem
        try:
            _validate_id_token(short_id, "alpha_id_short")
        except RegistryError as exc:
            self._load_errors.append(_LoadError(f"{zoo_id}.{short_id}", str(exc)))
            return

        try:
            meta = load_alpha_meta_from_py(py_file)
        except RegistryError as exc:
            self._load_errors.append(_LoadError(f"{zoo_id}.{short_id}", str(exc)))
            return

        module_path = f"aimoon.factors.zoo.{zoo_id}.{short_id}"
        alpha = Alpha(
            id=meta.id,
            zoo=zoo_id,
            module_path=module_path,
            meta={
                "nickname": meta.nickname,
                "theme": meta.theme,
                "columns_required": meta.columns_required,
                "extras_required": meta.extras_required,
                "requires_sector": meta.requires_sector,
                "universe": meta.universe,
                "min_warmup_bars": meta.min_warmup_bars,
                "decay_horizon": meta.decay_horizon,
                "formula_latex": meta.formula_latex,
                "frequency": meta.frequency,
                "notes": meta.notes,
            },
        )
        if alpha.id in self._alphas:
            self._load_errors.append(_LoadError(alpha.id, "duplicate alpha id"))
            return
        self._alphas[alpha.id] = alpha
        self._py_paths[alpha.id] = py_file

    # ── 公共 API ──

    def list(
        self,
        zoo: str | None = None,
        theme: str | None = None,
        universe: str | None = None,
    ) -> list[str]:
        """返回匹配过滤器的因子 ID 列表。"""
        out: list[str] = []
        for a in self._alphas.values():
            if zoo is not None and a.zoo != zoo:
                continue
            if theme is not None and theme not in a.meta.get("theme", []):
                continue
            if universe is not None and universe not in a.meta.get("universe", []):
                continue
            out.append(a.id)
        return sorted(out)

    def get(self, alpha_id: str) -> Alpha:
        if alpha_id not in self._alphas:
            raise KeyError(f"alpha_id {alpha_id!r} not in registry")
        return self._alphas[alpha_id]

    def health(self) -> dict[str, Any]:
        return {
            "loaded": len(self._alphas),
            "failed": len(self._load_errors),
            "errors": [{"alpha_id": e.alpha_id, "reason": e.reason} for e in self._load_errors],
        }

    def compute(self, alpha_id: str, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """惰性导入因子模块并执行 compute(panel)。失败时抛出 SkipAlphaError 或 RegistryError。"""
        alpha = self.get(alpha_id)
        meta = alpha.meta

        missing = [c for c in meta.get("columns_required", []) if c not in panel]
        if missing:
            raise SkipAlphaError(f"{alpha_id}: panel missing required columns {missing}")

        # Warmup check: skip factors that need more history than available.
        # A factor with min_warmup_bars=N needs N warmup rows before the first
        # valid value. For meaningful output we require at least 2*N rows
        # (otherwise >50% of output is NaN). Factors that only need a short
        # window (e.g. 21 bars) are always computed.
        min_warmup = meta.get("min_warmup_bars", 0)
        if min_warmup > 21 and "close" in panel:
            available = len(panel["close"])
            if available < min_warmup * 2:
                raise SkipAlphaError(
                    f"{alpha_id}: insufficient history — "
                    f"needs {min_warmup}*2={min_warmup * 2} bars for meaningful output, "
                    f"panel has {available}"
                )
        missing_extra = [c for c in meta.get("extras_required", []) if c not in panel]
        if missing_extra:
            raise SkipAlphaError(f"{alpha_id}: panel missing extras {missing_extra}")
        if meta.get("requires_sector") and "sector" not in panel:
            raise SkipAlphaError(f"{alpha_id}: panel missing sector tag")

        try:
            module = self._load_module(alpha)
        except Exception as exc:
            raise RegistryError(f"{alpha_id}: import failed: {exc}") from exc

        compute_fn = getattr(module, "compute", None)
        if compute_fn is None:
            raise RegistryError(f"{alpha_id}: module has no compute() function")

        try:
            result = compute_fn(panel)
        except Exception as exc:
            raise RegistryError(f"{alpha_id}: compute() raised: {exc}") from exc

        return self._validate_output(alpha_id, result, panel)

    def _load_module(self, alpha: Alpha) -> ModuleType:
        if not self._use_filesystem_loader:
            return importlib.import_module(alpha.module_path)
        py_file = self._py_paths[alpha.id]
        cached = sys.modules.get(alpha.module_path)
        if cached is not None and getattr(cached, "__file__", None) == str(py_file):
            return cached
        with _module_load_lock:
            # Double-check after acquiring lock
            cached = sys.modules.get(alpha.module_path)
            if cached is not None and getattr(cached, "__file__", None) == str(py_file):
                return cached
            spec = importlib.util.spec_from_file_location(alpha.module_path, py_file)
            if spec is None or spec.loader is None:
                raise RegistryError(f"{alpha.id}: could not build import spec for {py_file}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[alpha.module_path] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(alpha.module_path, None)
                raise
            return module

    @staticmethod
    def _validate_output(
        alpha_id: str,
        result: Any,
        panel: dict[str, pd.DataFrame],
    ) -> pd.DataFrame:
        if not isinstance(result, pd.DataFrame):
            raise RegistryError(
                f"{alpha_id}: compute() returned {type(result).__name__}, expected DataFrame"
            )
        ref = panel.get("close")
        if ref is not None and result.shape != ref.shape:
            raise RegistryError(
                f"{alpha_id}: output shape {result.shape} != close shape {ref.shape}"
            )
        arr = result.to_numpy(dtype=np.float64, na_value=np.nan)
        if np.isinf(arr).any():
            raise RegistryError(f"{alpha_id}: output contains +/- inf")
        nan_ratio = float(np.isnan(arr).mean()) if arr.size > 0 else 1.0
        if nan_ratio > 0.95:
            if alpha_id not in _nan_warned:
                _nan_warned.add(alpha_id)
                logger.warning(
                    "%s: output >95%% NaN (nan_ratio=%.3f) — returning result as-is; "
                    "factor will be filtered by ICIR weighter",
                    alpha_id,
                    nan_ratio,
                )
        return result

    def warmup(self) -> int:
        """预加载所有因子模块到 sys.modules，返回成功加载数。

        调用此方法可以避免首次 compute() 调用时的导入延迟。
        适合在 screen_universe 开始时调用，预热所有因子模块。
        """
        loaded = 0
        for alpha_id, alpha in self._alphas.items():
            try:
                self._load_module(alpha)
                loaded += 1
            except Exception as exc:
                logger.debug("Warmup skip %s: %s", alpha_id, exc)
        logger.info("Factor warmup: %d/%d modules loaded", loaded, len(self._alphas))
        return loaded


# ── 进程级单例 ──

_registry_cache: Registry | None = None
_registry_cache_lock = threading.Lock()


def get_default_registry() -> Registry:
    """返回进程级缓存的 Registry（线程安全）。"""
    global _registry_cache
    with _registry_cache_lock:
        if _registry_cache is None:
            _registry_cache = Registry()
        return _registry_cache


def reset_default_registry() -> None:
    """清除缓存的注册表（测试用）。"""
    global _registry_cache
    with _registry_cache_lock:
        _registry_cache = None


# Factor Theme Groups
_THEME_GROUPS = {
    "value": [
        "pe_ttm",
        "pb",
        "ps_ttm",
        "pcf_ncf_ttm",
        "market_cap",
        "dividend_yield",
        "total_mv",
        "circ_mv",
    ],
    "momentum": [
        "roc5",
        "roc10",
        "roc20",
        "roc60",
        "mom5",
        "mom10",
        "mom20",
        "mom60",
        "rsi6",
        "rsi12",
        "rsi24",
        "kdj_k",
        "kdj_d",
    ],
    "quality": [
        "roe_ttm",
        "roa_ttm",
        "gross_profit_margin",
        "net_profit_margin",
        "asset_turnover",
        "inventory_turnover",
        "receivables_turnover",
    ],
    "volatility": [
        "realized_vol_5",
        "realized_vol_10",
        "realized_vol_20",
        "atr_14",
        "bb_width",
        "realized_vol_60",
        "parkinson_vol",
    ],
    "growth": [
        "revenue_yoy",
        "profit_yoy",
        "roe_yoy",
        "total_revenue_yoy",
        "net_profit_yoy",
        "rd_expense_ratio",
    ],
    "sentiment": [
        "turnover_rate",
        "turnover_rate_f",
        "volume_ratio",
        "vwap_deviation",
        "moneyflow",
        "moneyflow_hsgt",
        "northbound_flow",
        "margin_balance",
    ],
}


def get_factor_theme(factor_id):
    """Get the theme group for a factor ID."""
    factor_lower = factor_id.lower()
    for theme, keywords in _THEME_GROUPS.items():
        for kw in keywords:
            if kw in factor_lower:
                return theme
    return "unknown"


def validate_factor_diversity(factor_ids, min_per_theme=3):
    """Check that factor selection covers all themes adequately."""
    import logging
    from collections import defaultdict

    theme_factors = defaultdict(list)
    for fid in factor_ids:
        theme = get_factor_theme(fid)
        theme_factors[theme].append(fid)
    for theme, factors in theme_factors.items():
        if theme != "unknown" and len(factors) < min_per_theme:
            logging.getLogger(__name__).warning(
                "Factor diversity: theme '%s' has only %d factors (min=%d)",
                theme,
                len(factors),
                min_per_theme,
            )
    return dict(theme_factors)
