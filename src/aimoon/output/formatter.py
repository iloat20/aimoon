"""Output formatting and display"""
from __future__ import annotations

import csv
import os
from datetime import datetime

import pandas as pd
from rich.console import Console
from rich.table import Table

from aimoon.config import CONFIG
from aimoon.strategies.screener import SignalScore


class OutputFormatter:
    def __init__(self) -> None:
        self.console = Console()

    def display_results(self, results: list[SignalScore]) -> None:
        if not results:
            self.console.print("[yellow]No stocks match the criteria[/yellow]")
            return
        table = Table(title=f"A-Share Quant Screen ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        table.add_column("No.", style="dim", width=4)
        table.add_column("Code", style="cyan", width=8)
        table.add_column("Name", style="bold", width=10)
        table.add_column("Price", justify="right", width=8)
        table.add_column("Chg%", justify="right", width=8)
        table.add_column("Turnover%", justify="right", width=8)
        table.add_column("Score", justify="right", width=6)
        table.add_column("Suggestion", width=10)
        table.add_column("Conf.", width=6)
        table.add_column("Signals", width=30)
        for i, r in enumerate(results, 1):
            ps = "green" if r.pct_change >= 0 else "red"
            ts = "bold green" if r.total_score >= 4 else ("yellow" if r.total_score >= 0 else "red")
            ss = "bold green" if "买" in r.suggestion else ("red" if "卖" in r.suggestion else "dim")
            table.add_row(
                str(i), r.stock_code, r.stock_name,
                f"{r.price:.2f}",
                f"[{ps}]{r.pct_change:+.2f}[/{ps}]",
                f"{r.turnover:.2f}",
                f"[{ts}]{r.total_score}[/{ts}]",
                f"[{ss}]{r.suggestion}[/{ss}]",
                r.confidence,
                " | ".join(r.signals) if r.signals else "-",
            )
        self.console.print(table)
        self.console.print(f"\n[dim]Total: {len(results)} stocks[/dim]")

    def export_csv(self, results: list[SignalScore], filename: str | None = None) -> str:
        if not filename:
            filename = f"screen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        os.makedirs(CONFIG.output_dir, exist_ok=True)
        filepath = os.path.join(CONFIG.output_dir, filename)
        rows = []
        for r in results:
            rows.append({
                "stock_code": r.stock_code, "stock_name": r.stock_name,
                "price": r.price, "pct_change": r.pct_change,
                "turnover": r.turnover, "pe": r.pe, "pb": r.pb,
                "total_market_cap_yi": r.total_market_cap_yi,
                "float_market_cap_yi": r.float_market_cap_yi,
                "trend_score": r.trend_score, "rsi_score": r.rsi_score,
                "macd_score": r.macd_score, "kdj_score": r.kdj_score,
                "volume_score": r.volume_score, "boll_score": r.boll_score,
                "momentum_score": r.momentum_score,
                "total_score": r.total_score,
                "signals": " | ".join(r.signals),
                "suggestion": r.suggestion, "confidence": r.confidence,
            })
        df = pd.DataFrame(rows)
        df.to_csv(filepath, index=False, encoding="utf-8-sig")
        return filepath