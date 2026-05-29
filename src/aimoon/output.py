"""输出格式化 — Rich 表格 + CSV + Markdown"""
from __future__ import annotations

import os
from datetime import datetime

import pandas as pd
from rich.console import Console
from rich.table import Table

from aimoon.config import Config
from aimoon.models import ScoredStock

# 类型标注用（避免循环导入）
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from aimoon.backtest import PortfolioBacktest
    from aimoon.factor_eval import FactorEval


class OutputFormatter:
    def __init__(self, cfg: Config | None = None) -> None:
        self.console = Console()
        self.cfg = cfg or Config()

    def display(self, results: list[ScoredStock]) -> None:
        if not results:
            self.console.print("[yellow]No stocks match the criteria[/yellow]")
            return
        table = Table(title=f"A-Share Quant Screen ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        table.add_column("No.", style="dim", width=4)
        table.add_column("Code", style="cyan", width=8)
        table.add_column("Name", style="bold", width=10)
        table.add_column("Price", justify="right", width=8)
        table.add_column("Chg%", justify="right", width=8)
        table.add_column("Score", justify="right", width=6)
        table.add_column("Suggestion", width=10)
        table.add_column("Conf.", width=6)
        table.add_column("Signals", width=30)
        for i, r in enumerate(results, 1):
            ps = "green" if r.pct_change >= 0 else "red"
            ts = "bold green" if r.total_score >= 4 else ("yellow" if r.total_score >= 0 else "red")
            sug, conf = r.suggestion
            ss = "bold green" if "买" in sug else ("red" if "卖" in sug else "dim")
            table.add_row(
                str(i), r.code, r.name, f"{r.price:.2f}",
                f"[{ps}]{r.pct_change:+.2f}[/{ps}]",
                f"[{ts}]{r.total_score}[/{ts}]",
                f"[{ss}]{sug}[/{ss}]", conf,
                " | ".join(s.label for s in r.signals) if r.signals else "-",
            )
        self.console.print(table)
        self.console.print(f"\n[dim]Total: {len(results)} stocks[/dim]")

    def export_csv(self, results: list[ScoredStock], filename: str | None = None) -> str:
        if not filename:
            filename = f"aimoon_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        os.makedirs(self.cfg.output_dir, exist_ok=True)
        filepath = os.path.join(self.cfg.output_dir, filename)
        rows = []
        for r in results:
            sug, conf = r.suggestion
            rows.append({
                "code": r.code, "name": r.name, "price": r.price,
                "pct_change": r.pct_change, "turnover": r.turnover,
                "pe": r.pe, "pb": r.pb, "market_cap_yi": r.market_cap_yi,
                "total_score": r.total_score, "suggestion": sug, "confidence": conf,
                "signals": " | ".join(s.label for s in r.signals),
                **r.rps,
            })
        pd.DataFrame(rows).to_csv(filepath, index=False, encoding="utf-8-sig")
        return filepath

    def export_markdown(self, results: list[ScoredStock], filename: str | None = None) -> str:
        if not filename:
            filename = f"aimoon_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        os.makedirs(self.cfg.output_dir, exist_ok=True)
        filepath = os.path.join(self.cfg.output_dir, filename)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [f"# A股量化筛选结果 {now}", "", f"共筛选 {len(results)} 只股票", ""]
        lines += ["| No. | Code | Name | Price | Score | Suggestion | Conf. | Signals |",
                  "|-----|------|------|-------|-------|------------|-------|---------|"]
        for i, r in enumerate(results, 1):
            sug, conf = r.suggestion
            sigs = " / ".join(s.label for s in r.signals).replace("|", "\\|") if r.signals else "-"
            lines.append(f"| {i} | {r.code} | {r.name} | {r.price:.2f} | {r.total_score} | {sug} | {conf} | {sigs} |")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return filepath

    def display_factor_eval(self, evals: list[FactorEval]) -> None:
        """显示因子评估报告。"""
        if not evals:
            self.console.print("[yellow]No factor evaluations to display[/yellow]")
            return
        table = Table(title="Factor Evaluation (IC/ICIR)")
        table.add_column("Factor", style="cyan", width=25)
        table.add_column("Mean IC", justify="right", width=10)
        table.add_column("ICIR", justify="right", width=8)
        table.add_column("IC>0%", justify="right", width=8)
        table.add_column("L-S", justify="right", width=8)
        table.add_column("Tier1", justify="right", width=8)
        table.add_column("Tier2", justify="right", width=8)
        table.add_column("Tier3", justify="right", width=8)
        table.add_column("Tier4", justify="right", width=8)
        table.add_column("Tier5", justify="right", width=8)
        table.add_column("Significance", width=12)
        for e in evals:
            ic_color = "green" if e.mean_ic > 0.03 else ("yellow" if e.mean_ic > 0 else "red")
            icir_color = "green" if abs(e.icir) > 0.5 else "dim"
            sig = "***" if abs(e.mean_ic) > 0.05 and abs(e.icir) > 0.5 else ("**" if abs(e.mean_ic) > 0.03 else ("*" if abs(e.mean_ic) > 0.02 else ""))
            tiers = []
            for t in e.tier_returns:
                color = "green" if t > 0 else "red"
                tiers.append(f"[{color}]{t:+.2f}[/{color}]")
            while len(tiers) < 5:
                tiers.append("-")
            table.add_row(
                e.name,
                f"[{ic_color}]{e.mean_ic:+.4f}[/{ic_color}]",
                f"[{icir_color}]{e.icir:+.2f}[/{icir_color}]",
                f"{e.ic_positive_ratio:.0%}",
                f"[{'green' if e.long_short > 0 else 'red'}]{e.long_short:+.2f}[/{'green' if e.long_short > 0 else 'red'}]",
                *tiers[:5],
                sig,
            )
        self.console.print(table)

    def display_portfolio_backtest(self, result: PortfolioBacktest) -> None:
        """显示组合回测报告。"""
        table = Table(title="Portfolio Backtest Results")
        table.add_column("Metric", style="cyan", width=20)
        table.add_column("Value", justify="right", width=12)

        def _color(v: float, invert: bool = False) -> str:
            if invert:
                return "green" if v < 0 else "red"
            return "green" if v > 0 else "red"

        table.add_row("Total Return", f"[{_color(result.total_return)}]{result.total_return:+.2f}%[/{_color(result.total_return)}]")
        table.add_row("Annual Return", f"[{_color(result.annual_return)}]{result.annual_return:+.2f}%[/{_color(result.annual_return)}]")
        table.add_row("Sharpe Ratio", f"[{_color(result.sharpe_ratio)}]{result.sharpe_ratio:+.2f}[/{_color(result.sharpe_ratio)}]")
        table.add_row("Max Drawdown", f"[{_color(result.max_drawdown, True)}]{result.max_drawdown:.2f}%[/{_color(result.max_drawdown, True)}]")
        table.add_row("Calmar Ratio", f"{result.calmar_ratio:+.2f}")
        table.add_row("Win Rate", f"{result.win_rate:.1%}")
        table.add_row("Trade Count", str(result.trade_count))
        table.add_row("Avg Hold Days", f"{result.avg_hold_days:.0f}")
        table.add_row("Turnover Rate", f"{result.turnover_rate:.2f}")
        if result.benchmark_return != 0.0:
            table.add_row("Benchmark Return", f"{result.benchmark_return:+.2f}%")
            table.add_row("Excess Return", f"[{_color(result.excess_return)}]{result.excess_return:+.2f}%[/{_color(result.excess_return)}]")
        self.console.print(table)
