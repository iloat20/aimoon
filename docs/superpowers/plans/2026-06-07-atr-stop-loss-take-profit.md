# ATR 动态止损止盈实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) for syntax tracking.

**Goal:** Replace fixed-percentage stop-loss (4%) and take-profit (15%) with ATR(14)-based dynamic levels to improve win rate (43.8% → 50%+) and profit/loss ratio (0.82 → 1.5+).

**Architecture:** Add ATR multiplier constants to risk_controls.py, add ATR-based stop/tp helper functions, modify EnhancedBacktestEngine to use adaptive levels at entry and exit, wire CLI/config params.

**Tech Stack:** Python 3.12+, pandas, ATR(14) from TechInd indicator.

**Design doc:** `docs/superpowers/specs/2026-06-07-atr-stop-loss-take-profit-design.md`

---

### Task 1: Add ATR multiplier constants to risk_controls.py

**Files:**
- Modify: `src/aimoon/backtest/risk_controls.py`

- [ ] **Step 1: Add ATR multiplier constants after existing parameters**

Modify `src/aimoon/backtest/risk_controls.py` — add these constants after the `CHANDELIER_ATR_MULTIPLIER` line:

```python
# ── ATR 动态止损止盈参数 ──
STOP_LOSS_ATR_MULTIPLIER: float = 1.0    # 止损 ATR 倍数（1.0×ATR）
TAKE_PROFIT_ATR_MULTIPLIER: float = 4.0  # 止盈 ATR 倍数（4.0×ATR）
_MIN_STOP_LOSS_PCT: float = 0.02         # 最小止损（2%，低波动保护）
_MAX_STOP_LOSS_PCT: float = 0.06         # 最大止损（6%，高波动保护）
_MIN_TAKE_PROFIT_PCT: float = 0.09       # 最小止盈（9%，低波动保底收益）
_MAX_TAKE_PROFIT_PCT: float = 0.18       # 最大止盈（18%，高波动利润保护）
```

- [ ] **Step 2: Add ATR-based stop-loss and take-profit helper functions**

Add before the `compute_trailing_stop` function:

```python
def compute_atr_stop_loss(atr_pct: float, multiplier: float = STOP_LOSS_ATR_MULTIPLIER) -> float:
    """Compute ATR-based stop-loss percentage.

    Args:
        atr_pct: ATR as percentage of entry price (e.g., 4.0 means 4%).
        multiplier: ATR multiplier.

    Returns:
        Stop-loss percentage clamped to [_MIN_STOP_LOSS_PCT, _MAX_STOP_LOSS_PCT].
    """
    raw = atr_pct * multiplier / 100.0  # Convert percentage to decimal
    return max(_MIN_STOP_LOSS_PCT, min(_MAX_STOP_LOSS_PCT, raw))


def compute_atr_take_profit(atr_pct: float, multiplier: float = TAKE_PROFIT_ATR_MULTIPLIER) -> float:
    """Compute ATR-based take-profit percentage.

    Args:
        atr_pct: ATR as percentage of entry price (e.g., 4.0 means 4%).
        multiplier: ATR multiplier.

    Returns:
        Take-profit percentage clamped to [_MIN_TAKE_PROFIT_PCT, _MAX_TAKE_PROFIT_PCT].
    """
    raw = atr_pct * multiplier / 100.0  # Convert percentage to decimal
    return max(_MIN_TAKE_PROFIT_PCT, min(_MAX_TAKE_PROFIT_PCT, raw))
```

- [ ] **Step 3: Verify with ruff**

```bash
ruff check src/aimoon/backtest/risk_controls.py
Expected: no errors
```

- [ ] **Step 4: Commit**

```bash
git add src/aimoon/backtest/risk_controls.py
git commit -m "feat(risk): add ATR multiplier constants for dynamic stop/tp"
```

---

### Task 2: Add ATR helpers to enhanced_backtest.py

**Files:**
- Modify: `src/aimoon/enhanced_backtest.py` (add standalone helper functions after the import block)

- [ ] **Step 1: Import new risk_controls constants**

In `src/aimoon/enhanced_backtest.py`, after existing `_REGIME_TAKE_PROFIT` import, add:

```python
from aimoon.backtest.risk_controls import (
    CHANDELIER_ATR_MULTIPLIER,
    DD_THRESHOLDS,
    HARD_LOSS_CAP,
    PROFIT_PROTECTION_FLOOR,
    PROFIT_PROTECTION_PEAK_THRESHOLD,
    REGIME_TAKE_PROFIT,
    TIME_DECAY_IDLE_DAYS,
    TIME_DECAY_LOSS_DAYS,
    TIME_DECAY_TIGHTEN_RATIO,
    TRAILING_STOP_TIERS,
    # New imports for ATR-based stop/tp
    STOP_LOSS_ATR_MULTIPLIER,
    TAKE_PROFIT_ATR_MULTIPLIER,
    _MIN_STOP_LOSS_PCT,
    _MAX_STOP_LOSS_PCT,
    _MIN_TAKE_PROFIT_PCT,
    _MAX_TAKE_PROFIT_PCT,
    compute_atr_stop_loss,
    compute_atr_take_profit,
)
```

Only add the NEW ones (STOP_LOSS_ATR_MULTIPLIER, TAKE_PROFIT_ATR_MULTIPLIER, compute_atr_stop_loss, compute_atr_take_profit) to the existing import block, plus the min/max pct constants if they're not already present in the `from aimoon.backtest.risk_controls import (...)` block.

Note: Check the existing import block at the top of enhanced_backtest.py. It imports specifics via `from aimoon.backtest import _detect_regime_safe, risk_controls` and separately imports named constants. Use `edit` to add to the named constant imports.

- [ ] **Step 2: Rename _compute_dynamic_stop_loss → _compute_atr_entry_stop_loss**

Replace `_compute_dynamic_stop_loss` function at the bottom of the file (line 1663) with the new ATR-based version that uses the risk_controls constants:

```python
def _compute_atr_entry_stop_loss(kline: pd.DataFrame, atr_multiplier: float, fallback: float = 0.04) -> float:
    """Compute ATR-based stop-loss at entry time.

    Uses ATR(14) percentage × multiplier, clamped to [2%, 6%].

    Args:
        kline: K-line DataFrame with OHLCV.
        atr_multiplier: ATR multiplier (default STOP_LOSS_ATR_MULTIPLIER=1.0).
        fallback: Fallback stop-loss if ATR unavailable.

    Returns:
        Stop-loss as decimal (e.g., 0.04 = 4%).
    """
    try:
        if len(kline) < 20:
            return fallback
        ti = TechInd(kline)
        atr = ti.atr(14)
        close = float(kline["close"].iloc[-1]) if "close" in kline.columns else 1.0
        if close <= 0 or atr is None:
            return fallback
        atr_val = float(atr.iloc[-1]) if hasattr(atr, 'iloc') else float(atr)
        atr_pct = atr_val / close * 100.0
        return compute_atr_stop_loss(atr_pct, atr_multiplier)
    except Exception:
        return fallback


def _compute_atr_take_profit(kline: pd.DataFrame, atr_multiplier: float, fallback: float = 0.15) -> float:
    """Compute ATR-based take-profit at entry time.

    Uses ATR(14) percentage × multiplier, clamped to [9%, 18%].

    Args:
        kline: K-line DataFrame with OHLCV.
        atr_multiplier: ATR multiplier (default TAKE_PROFIT_ATR_MULTIPLIER=4.0).
        fallback: Fallback take-profit if ATR unavailable.

    Returns:
        Take-profit as decimal (e.g., 0.15 = 15%).
    """
    try:
        if len(kline) < 20:
            return fallback
        ti = TechInd(kline)
        atr = ti.atr(14)
        close = float(kline["close"].iloc[-1]) if "close" in kline.columns else 1.0
        if close <= 0 or atr is None:
            return fallback
        atr_val = float(atr.iloc[-1]) if hasattr(atr, 'iloc') else float(atr)
        atr_pct = atr_val / close * 100.0
        return compute_atr_take_profit(atr_pct, atr_multiplier)
    except Exception:
        return fallback
```

Remove the old `_compute_dynamic_stop_loss` function entirely.

- [ ] **Step 3: Verify with ruff**

```bash
ruff check src/aimoon/enhanced_backtest.py
Expected: no errors
```

- [ ] **Step 4: Commit**

```bash
git add src/aimoon/enhanced_backtest.py
git commit -m "feat(backtest): add ATR stop/tp helper functions"
```

---

### Task 3: Integrate ATR stop/tp into EnhancedBacktestEngine

**Files:**
- Modify: `src/aimoon/enhanced_backtest.py`

- [ ] **Step 1: Add ATR multiplier parameters to __init__**

In `EnhancedBacktestEngine.__init__`, add after `stop_loss_pct` and `take_profit_pct`:

```python
self.stop_loss_atr_multiplier: float = kwargs.get("stop_loss_atr_multiplier", STOP_LOSS_ATR_MULTIPLIER)
self.take_profit_atr_multiplier: float = kwargs.get("take_profit_atr_multiplier", TAKE_PROFIT_ATR_MULTIPLIER)
```

Update the `__init__` signature to include the new parameters:

```python
def __init__(
    self,
    ...
    stop_loss_pct: float = 0.04,
    take_profit_pct: float = 0.15,
    stop_loss_atr_multiplier: float = STOP_LOSS_ATR_MULTIPLIER,  # NEW
    take_profit_atr_multiplier: float = TAKE_PROFIT_ATR_MULTIPLIER,  # NEW
    ...
)
```

- [ ] **Step 2: Update _phase0_execute_pending to use ATR stop-loss**

In `_phase0_execute_pending`, replace the line:

```python
dynamic_sl = _compute_dynamic_stop_loss(entry_window, self.stop_loss_pct)
```

with:

```python
dynamic_sl = _compute_atr_entry_stop_loss(entry_window, self.stop_loss_atr_multiplier, self.stop_loss_pct)
```

- [ ] **Step 3: Update _phase1_stop_loss_take_profit to use ATR take-profit**

In `_phase1_stop_loss_take_profit`, find the take-profit check (around line 804):

```python
elif pnl >= _regime_take_profit(current_regime, self.take_profit_pct):
```

Replace with ATR-based take profit. Add a helper at the top of the method to compute the dynamic take profit level using the stored ATR at entry:

Calculate the take-profit percentage from ATR:

```python
# Compute ATR-based take-profit threshold at entry for this position
# Store on the position so it's consistent throughout the trade
pos_tp_pct = pos.atr_at_entry if hasattr(pos, 'atr_at_entry') and pos.atr_at_entry > 0 else self.take_profit_pct
```

The take-profit condition should use the stored `atr_at_entry` to compute a dynamic TP level:

```python
# ── ATR-based take-profit ──
# Use the ATR at entry to compute dynamic take-profit threshold
if pos.atr_at_entry > 0:
    entry_atr_pct = pos.atr_at_entry / pos.entry_price * 100.0
    atr_tp = compute_atr_take_profit(entry_atr_pct, self.take_profit_atr_multiplier)
else:
    atr_tp = self.take_profit_pct
tp_threshold = _regime_take_profit(current_regime, atr_tp)
```

Then replace the take-profit condition:

```python
elif pnl >= tp_threshold:
    to_close.append((code, current_price, "take_profit", elapsed_days))
```

- [ ] **Step 4: Verify with ruff**

```bash
ruff check src/aimoon/enhanced_backtest.py
Expected: no errors
```

- [ ] **Step 5: Enhance Chandelier exit to work for all trades**

In `_phase1_stop_loss_take_profit`, find the Chandelier Exit code block:

```python
# ── Chandelier Exit: ATR-based adaptive trailing stop ──
atr_val = pos.atr_at_entry if pos.atr_at_entry > 0 else 0
if atr_val > 0 and pnl > 0:
    highest = pos.highest_price if pos.highest_price > 0 else current_price
    chandelier_stop = (
        highest - _CHANDELIER_ATR_MULTIPLIER * atr_val
    ) / pos.entry_price - 1
    if chandelier_stop > 0:
        effective_sl = max(effective_sl, chandelier_stop)
```

Replace with enhanced version that activates for all trades (not just profitable):

```python
# ── Enhanced Chandelier Exit: ATR-based adaptive trailing stop ──
# Activated for all trades (not just profitable ones).
# Uses highest price since entry minus 2.5x ATR as trailing floor.
atr_val = pos.atr_at_entry if pos.atr_at_entry > 0 else 0
if atr_val > 0:
    highest = pos.highest_price if pos.highest_price > 0 else current_price
    chandelier_stop = (
        highest - _CHANDELIER_ATR_MULTIPLIER * atr_val
    ) / pos.entry_price - 1
    # Chandelier provides tighter trailing stop when profitable
    # and wider protection when near breakeven
    effective_sl = max(effective_sl, chandelier_stop)
```

- [ ] **Step 6: Verify with ruff**

```bash
ruff check src/aimoon/enhanced_backtest.py
Expected: no errors
```

- [ ] **Step 7: Commit**

```bash
git add src/aimoon/enhanced_backtest.py
git commit -m "feat(backtest): integrate ATR stop/tp into engine phases"
```

---

### Task 4: Add CLI and config parameters

**Files:**
- Modify: `src/aimoon/cli.py`
- Modify: `src/aimoon/config.py`

- [ ] **Step 1: Add config fields to Config dataclass**

In `src/aimoon/config.py`, add to the `Config` frozen dataclass:

```python
stop_loss_atr_multiplier: float = 1.0  # ATR 止损倍数
take_profit_atr_multiplier: float = 4.0  # ATR 止盈倍数
```

- [ ] **Step 2: Add CLI arguments**

In `src/aimoon/cli.py`, find the backtest argument parser and add:

```python
backtest_parser.add_argument(
    "--sl-atr", type=float, default=None,
    help="ATR 止损倍数（默认 1.0，0=使用固定百分比）",
)
backtest_parser.add_argument(
    "--tp-atr", type=float, default=None,
    help="ATR 止盈倍数（默认 4.0，0=使用固定百分比）",
)
```

In the backtest command handler, pass the values to `EnhancedBacktestEngine`:

```python
sl_atr = args.sl_atr if args.sl_atr is not None else cfg.stop_loss_atr_multiplier
tp_atr = args.tp_atr if args.tp_atr is not None else cfg.take_profit_atr_multiplier
```

- [ ] **Step 3: Verify with ruff**

```bash
ruff check src/aimoon/cli.py src/aimoon/config.py
Expected: no errors
```

- [ ] **Step 4: Commit**

```bash
git add src/aimoon/cli.py src/aimoon/config.py
git commit -m "feat(cli): add --sl-atr and --tp-atr backtest parameters"
```

---

### Task 5: Run backtest and verify performance

**Files:**
- Run: backtest command

- [ ] **Step 1: Run a backtest with new ATR parameters**

```bash
aimoon backtest --sl-atr 1.0 --tp-atr 4.0
```

Expected: backtest completes without errors. Check the output report for:
- Win Rate >= 50%
- Profit/Loss Ratio >= 1.5
- stop_loss trades reduced (currently 44%)
- take_profit trades increased (currently 25%)

- [ ] **Step 2: Run code quality checks**

```bash
ruff check src/aimoon/backtest/risk_controls.py src/aimoon/enhanced_backtest.py src/aimoon/cli.py src/aimoon/config.py
Expected: 0 errors

black --check src/aimoon/backtest/risk_controls.py src/aimoon/enhanced_backtest.py src/aimoon/cli.py src/aimoon/config.py
Expected: All files already formatted
```

- [ ] **Step 3: Commit final changes**

```bash
git add -A
git commit -m "feat: ATR dynamic stop-loss/take-profit backtest complete"
```

---

## Spec Coverage Check

| Spec Requirement | Task |
|-----------------|------|
| ADD STOP_LOSS_ATR_MULTIPLIER=1.0 | Task 1 |
| ADD TAKE_PROFIT_ATR_MULTIPLIER=4.0 | Task 1 |
| Add clamp bounds [2%,6%] / [9%,18%] | Task 1 |
| compute_atr_stop_loss() helper | Task 1 |
| compute_atr_take_profit() helper | Task 1 |
| Add helpers to enhanced_backtest.py | Task 2 |
| Remove old _compute_dynamic_stop_loss | Task 2 |
| Add ATR params to Engine __init__ | Task 3 |
| Use ATR stop in _phase0_execute_pending | Task 3 |
| Use ATR take-profit in _phase1 | Task 3 |
| CLI --sl-atr / --tp-atr | Task 4 |
| Config dataclass fields | Task 4 |
| Verify backtest performance | Task 5 |
| Code quality checks | Task 5 |
