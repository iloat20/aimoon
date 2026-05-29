"""输出格式化 — Rich 表格 + CSV + Markdown"""
from __future__ import annotations

import os
from datetime import datetime

import pandas as pd
from rich.console import Console
from rich.table import Table

from aimoon.config import Config
from aimoon.models import ScoredStock


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
