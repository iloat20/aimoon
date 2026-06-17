"""输出格式化 — Rich 表格 + CSV + Markdown"""

from __future__ import annotations

import os
from datetime import datetime

# 类型标注用（避免循环导入）
from typing import TYPE_CHECKING

import pandas as pd
from rich.console import Console
from rich.table import Table

from aimoon.config import Config
from aimoon.models import ScoredStock
from aimoon.scoring.hybrid_scorer import get_suggestion

if TYPE_CHECKING:
    from aimoon.factor_eval import FactorEval


def _color_value(v: float, invert: bool = False) -> str:
    """Return 'green' or 'red' Rich color tag based on value sign."""
    if invert:
        return "green" if v < 0 else "red"
    return "green" if v > 0 else "red"


class OutputFormatter:
    def __init__(self, cfg: Config | None = None) -> None:
        self.console = Console(force_terminal=True)
        self.cfg = cfg or Config()

    def display(self, results: list[ScoredStock], turtle_plans: dict | None = None) -> None:
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
        table.add_column("Turtle", width=12)
        table.add_column("Suggestion", width=10)
        table.add_column("Conf.", width=6)
        for i, r in enumerate(results, 1):
            ps = "green" if r.pct_change >= 0 else "red"
            ts = (
                "bold green"
                if r.total_score >= 65
                else ("yellow" if r.total_score >= 35 else "red")
            )
            sug, conf = get_suggestion(r.total_score)
            ss = "bold green" if "买" in sug else ("red" if "卖" in sug else "dim")
            # Turtle signal
            plan = (turtle_plans or {}).get(r.code)
            if plan is not None:
                ttype = plan.signal_type
                if ttype == "buy":
                    turtle_str = f"[bold green]▲买{plan.entry_price:.1f}[/bold green]"
                elif ttype == "add":
                    turtle_str = f"[green]＋{plan.entry_price:.1f}[/green]"
                elif ttype == "close":
                    turtle_str = f"[red]▼卖{plan.exit_price:.1f}[/red]"
                else:
                    turtle_str = "[dim]─[/dim]"
            else:
                turtle_str = "[dim]─[/dim]"
            table.add_row(
                str(i),
                r.code,
                r.name,
                f"{r.price:.2f}",
                f"[{ps}]{r.pct_change:+.2f}[/{ps}]",
                f"[{ts}]{r.total_score}[/{ts}]",
                turtle_str,
                f"[{ss}]{sug}[/{ss}]",
                conf,
            )
        self.console.print(table)
        self.console.print(f"\n[dim]Total: {len(results)} stocks[/dim]")

    def display_turtle_plans(self, turtle_plans: dict) -> None:
        """Display detailed Turtle trading plans with specific prices."""
        if not turtle_plans:
            return

        table = Table(title="Super Turtle 交易计划")
        table.add_column("Code", style="cyan", width=8)
        table.add_column("Name", style="bold", width=10)
        table.add_column("Signal", width=8)
        table.add_column("当前价", justify="right", width=8)
        table.add_column("买入价", justify="right", width=8)
        table.add_column("止损价", justify="right", width=8)
        table.add_column("加仓价", justify="right", width=14)
        table.add_column("目标1", justify="right", width=8)
        table.add_column("目标2", justify="right", width=8)
        table.add_column("清仓价", justify="right", width=8)
        table.add_column("跟踪止损", justify="right", width=8)
        table.add_column("每单位", justify="right", width=8)

        for code, plan in turtle_plans.items():
            sig_color = (
                "bold green"
                if plan.signal_type == "buy"
                else ("red" if plan.signal_type == "close" else "dim")
            )
            sig_text = (
                "▲买入"
                if plan.signal_type == "buy"
                else ("▼卖出" if plan.signal_type == "close" else "─")
            )

            add_str = ",".join(f"{p:.1f}" for p in plan.add_prices) if plan.add_prices else "-"
            chandelier_str = f"{plan.chandelier_stop:.2f}" if plan.chandelier_stop > 0 else "-"

            table.add_row(
                plan.code,
                plan.name,
                f"[{sig_color}]{sig_text}[/{sig_color}]",
                f"{plan.current_price:.2f}",
                f"{plan.entry_price:.2f}",
                f"{plan.entry_stop_loss:.2f}",
                add_str,
                f"{plan.tp1_price:.2f}",
                f"{plan.tp2_price:.2f}",
                f"{plan.exit_price:.2f}",
                chandelier_str,
                f"{plan.shares_per_unit}股",
            )

        self.console.print(table)

    def export_csv(self, results: list[ScoredStock], filename: str | None = None) -> str:
        if not filename:
            filename = f"aimoon_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        os.makedirs(self.cfg.output_dir, exist_ok=True)
        filepath = os.path.join(self.cfg.output_dir, filename)
        rows = []
        for r in results:
            sug, conf = get_suggestion(r.total_score)
            rows.append(
                {
                    "code": r.code,
                    "name": r.name,
                    "price": r.price,
                    "pct_change": r.pct_change,
                    "turnover": r.turnover,
                    "pe": r.pe,
                    "pb": r.pb,
                    "market_cap_yi": r.market_cap_yi,
                    "total_score": r.total_score,
                    "suggestion": sug,
                    "confidence": conf,
                    **r.rps,
                }
            )
        pd.DataFrame(rows).to_csv(filepath, index=False, encoding="utf-8-sig")
        return filepath

    def export_markdown(
        self, results: list[ScoredStock], filename: str | None = None, regime: str | None = None
    ) -> str:
        from aimoon.scoring import hybrid_score

        if not filename:
            filename = f"aimoon_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        os.makedirs(self.cfg.output_dir, exist_ok=True)
        filepath = os.path.join(self.cfg.output_dir, filename)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [f"# A股量化筛选报告 {now}", ""]

        # Market regime
        if regime:
            lines += [f"**市场状态：{regime}**", ""]

        # Scored with capped score for ranking
        ranked = sorted(results, key=lambda s: hybrid_score(list(s.signals)), reverse=True)

        # Trading advice sections
        top1 = ranked[0] if ranked else None
        if top1:
            top1_score = hybrid_score(list(top1.signals))
            lines += [
                "## 交易建议",
                "",
                "### 首选买入",
                "",
                f"**{top1.code} {top1.name}** — 价格 {top1.price:.2f}，分类评分 {top1_score}",
                "",
                f"- 止损：{self.cfg.stop_loss_pct:.0%}  止盈：{self.cfg.take_profit_pct:.0%}  持仓上限：{self.cfg.hold_days}天",
                "",
            ]

        # Strong buy candidates (top 5)
        strong_buy = [s for s in ranked if hybrid_score(list(s.signals)) >= 15][:5]
        if len(strong_buy) > 1:
            lines += [
                "### 强势候选（前5）",
                "",
                "| 代码 | 名称 | 价格 | 分类评分 | 建议 |",
                "|------|------|------|----------|------|",
            ]
            for s in strong_buy:
                cs = hybrid_score(list(s.signals))
                sug, conf = get_suggestion(s.total_score)
                lines.append(f"| {s.code} | {s.name} | {s.price:.2f} | {cs} | {sug} |")
            lines.append("")

        # Full ranked table
        lines += [
            "## 完整筛选结果",
            "",
            f"共筛选 {len(ranked)} 只股票（按分类评分排序）",
            "",
            "| No. | Code | Name | Price | CapScore | RawScore | Suggestion |",
            "|-----|------|------|-------|----------|----------|------------|",
        ]
        for i, r in enumerate(ranked, 1):
            sug, conf = get_suggestion(r.total_score)
            cs = hybrid_score(list(r.signals))
            lines.append(
                f"| {i} | {r.code} | {r.name} | {r.price:.2f} | {cs} | {r.total_score} | {sug} |"
            )

        lines += ["", f"---\n\n*报告生成时间: {now}*"]
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
            sig = (
                "***"
                if abs(e.mean_ic) > 0.05 and abs(e.icir) > 0.5
                else ("**" if abs(e.mean_ic) > 0.03 else ("*" if abs(e.mean_ic) > 0.02 else ""))
            )
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

    def display_portfolio_backtest(self, result) -> None:
        """显示组合回测报告。"""
        table = Table(title="Portfolio Backtest Results")
        table.add_column("Metric", style="cyan", width=20)
        table.add_column("Value", justify="right", width=12)
        metrics = {
            "Total Return": f"{getattr(result, 'total_return', 0):+.2%}",
            "Sharpe": f"{getattr(result, 'sharpe', 0):.2f}",
            "Max Drawdown": f"{getattr(result, 'max_drawdown', 0):.2%}",
            "Win Rate": f"{getattr(result, 'win_rate', 0):.1%}",
        }
        for k, v in metrics.items():
            table.add_row(k, v)
        self.console.print(table)

    def display_qf_backtest(self, result) -> None:
        """显示 QF-Lib 事件驱动回测报告。"""
        table = Table(title="QF-Lib Event-Driven Backtest")
        table.add_column("Metric", style="cyan", width=20)
        table.add_column("Value", justify="right", width=12)

        metrics = {
            "Total Return": f"{getattr(result, 'total_return_pct', 0):+.2f}%",
            "Annual Return": f"{getattr(result, 'annual_return_pct', 0):+.2f}%",
            "Sharpe": f"{getattr(result, 'sharpe_ratio', 0):.2f}",
            "Sortino": f"{getattr(result, 'sortino_ratio', 0):.2f}",
            "Max Drawdown": f"{getattr(result, 'max_drawdown_pct', 0):.2f}%",
            "Win Rate": f"{getattr(result, 'win_rate', 0):.1f}%",
            "Profit Factor": f"{getattr(result, 'profit_factor', 0):.2f}",
            "Avg Win": f"{getattr(result, 'avg_win_pct', 0):+.2f}%",
            "Avg Loss": f"{getattr(result, 'avg_loss_pct', 0):+.2f}%",
            "Trade Count": f"{getattr(result, 'trade_count', 0)}",
            "Calmar Ratio": f"{getattr(result, 'calmar_ratio', 0):.2f}",
        }
        # IC tracking
        ic_mean = getattr(result, "ic_mean", 0.0)
        ic_std = getattr(result, "ic_std", 0.0)
        ic_pos = getattr(result, "ic_positive_pct", 0.0)
        if ic_mean != 0.0 or ic_std != 0.0:
            metrics["IC Mean"] = f"{ic_mean:.4f}"
            metrics["IC Std"] = f"{ic_std:.4f}"
            metrics["IC > 0 %"] = f"{ic_pos:.1f}%"
            icir = ic_mean / ic_std if ic_std > 1e-10 else 0.0
            metrics["ICIR"] = f"{icir:.2f}"
        for k, v in metrics.items():
            table.add_row(k, v)
        self.console.print(table)

    def display_enhanced_backtest(self, result) -> None:
        """显示增强回测报告（Sortino/盈亏比/基准对比）。"""
        table = Table(title="Enhanced Portfolio Backtest")
        table.add_column("Metric", style="cyan", width=20)
        table.add_column("Value", justify="right", width=12)
        # IC 指标（评分系统的预测力评估）
        ic_series = getattr(result, "ic_series", ())
        avg_ic = sum(ic_series) / len(ic_series) if ic_series else 0.0
        icir = (
            avg_ic / (sum((x - avg_ic) ** 2 for x in ic_series) / max(len(ic_series) - 1, 1)) ** 0.5
            if len(ic_series) > 1
            else 0.0
        )

        metrics = {
            "Total Return": f"{getattr(result, 'total_return', 0):+.2%}",
            "Annual Return": f"{getattr(result, 'annual_return', 0):+.2%}",
            "Sharpe": f"{getattr(result, 'sharpe_ratio', 0):.2f}",
            "Sortino": f"{getattr(result, 'sortino_ratio', 0):.2f}",
            "Max Drawdown": f"{getattr(result, 'max_drawdown', 0):.2%}",
            "Win Rate": f"{getattr(result, 'win_rate', 0):.1%}",
            "Profit Factor": f"{getattr(result, 'profit_factor', 0):.2f}",
            "Avg Win": f"{getattr(result, 'avg_win', 0):+.2%}",
            "Avg Loss": f"{getattr(result, 'avg_loss', 0):+.2%}",
            "Trade Count": f"{getattr(result, 'trade_count', 0)}",
            "Avg Hold Days": f"{getattr(result, 'avg_hold_days', 0):.0f}",
        }
        # 预测力指标（仅当有 IC 数据时显示）
        if ic_series:
            ic_color = "green" if avg_ic > 0.03 else ("yellow" if avg_ic > 0 else "red")
            metrics["Avg IC (Pred)"] = f"[{ic_color}]{avg_ic:+.4f}[/{ic_color}]"
            metrics["ICIR (Pred)"] = f"[{ic_color}]{icir:+.2f}[/{ic_color}]"
            metrics["IC Samples"] = str(len(ic_series))
        for k, v in metrics.items():
            table.add_row(k, v)
        self.console.print(table)

    def display_optimize(self, results) -> None:
        """显示参数优化结果。"""
        if not results:
            self.console.print("[yellow]No optimization results.[/yellow]")
            return
        table = Table(title="Parameter Optimization Results")
        table.add_column("Rank", style="dim", width=4)
        table.add_column("Params", style="cyan", width=30)
        table.add_column("Sharpe", justify="right", width=8)
        table.add_column("Sortino", justify="right", width=8)
        table.add_column("Return%", justify="right", width=8)
        table.add_column("MaxDD%", justify="right", width=8)
        table.add_column("Trades", justify="right", width=6)

        for i, r in enumerate(results[:20], 1):
            params_str = ", ".join(f"{k}={v}" for k, v in sorted(r.params.items()))
            sc = "green" if r.sharpe > 0 else "red"
            rc = "green" if r.total_return > 0 else "red"
            table.add_row(
                str(i),
                params_str,
                f"[{sc}]{r.sharpe:+.2f}[/{sc}]",
                f"{r.sortino:+.2f}",
                f"[{rc}]{r.total_return:+.2f}[/{rc}]",
                f"{r.max_drawdown:.2f}",
                str(r.trade_count),
            )
        self.console.print(table)

    def display_walk_forward(self, result) -> None:
        """显示 Walk-Forward 验证结果。"""
        if not result.splits:
            self.console.print("[yellow]Not enough data for walk-forward validation.[/yellow]")
            return
        table = Table(title="Walk-Forward Validation")
        table.add_column("Split", style="dim", width=5)
        table.add_column("Train Period", style="cyan", width=22)
        table.add_column("Test Period", style="cyan", width=22)
        table.add_column("Train Sharpe", justify="right", width=10)
        table.add_column("Test Sharpe", justify="right", width=10)
        table.add_column("Test Return%", justify="right", width=10)
        table.add_column("Test MaxDD%", justify="right", width=10)

        for s in result.splits:
            sc = "green" if s.test_sharpe > 0 else "red"
            rc = "green" if s.test_return > 0 else "red"
            table.add_row(
                str(s.split_idx + 1),
                f"{s.train_start[:10]} ~ {s.train_end[:10]}",
                f"{s.test_start[:10]} ~ {s.test_end[:10]}",
                f"{s.train_sharpe:+.2f}",
                f"[{sc}]{s.test_sharpe:+.2f}[/{sc}]",
                f"[{rc}]{s.test_return:+.2f}[/{rc}]",
                f"{s.test_max_dd:.2f}",
            )
        self.console.print(table)

        # Summary
        summary = Table(title="Walk-Forward Summary", show_header=False)
        summary.add_column("Metric", style="cyan", width=20)
        summary.add_column("Value", justify="right", width=12)
        sc = "green" if result.avg_test_sharpe > 0 else "red"
        summary.add_row("Stability Score", f"[{sc}]{result.stability_score:+.2f}[/{sc}]")
        summary.add_row("Avg Test Sharpe", f"[{sc}]{result.avg_test_sharpe:+.2f}[/{sc}]")
        rc = "green" if result.avg_test_return > 0 else "red"
        summary.add_row("Avg Test Return", f"[{rc}]{result.avg_test_return:+.2f}%[/{rc}]")
        self.console.print(summary)

    def export_backtest_report(
        self,
        result,
        top_stocks: list,
        cfg,
        filename: str | None = None,
    ) -> str:
        """生成完整回测报告 Markdown 文档，用于参数调优。"""
        if not filename:
            filename = f"backtest_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        os.makedirs(self.cfg.output_dir, exist_ok=True)
        filepath = os.path.join(self.cfg.output_dir, filename)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            f"# A股量化回测报告 {now}",
            "",
            "---",
            "",
            "## 一、回测参数",
            "",
            "| 参数 | 值 | 说明 |",
            "|------|----|------|",
            f"| history_days | {cfg.history_days} | 历史数据天数 |",
            f"| hold_days | {cfg.hold_days} | 持仓天数 |",
            f"| max_positions | {cfg.max_positions} | 最大持仓数 |",
            f"| top_n | {cfg.top_n} | 筛选候选数 |",
            f"| stop_loss_pct | {cfg.stop_loss_pct:.0%} | 止损比例 |",
            f"| take_profit_pct | {cfg.take_profit_pct:.0%} | 止盈比例 |",
            f"| rsi_period | {cfg.rsi_period} | RSI 周期 |",
            f"| macd_fast/slow/signal | {cfg.macd_fast}/{cfg.macd_slow}/{cfg.macd_signal} | MACD 参数 |",
            f"| kdj_period | {cfg.kdj_period} | KDJ 周期 |",
            f"| boll_period/std | {cfg.boll_period}/{cfg.boll_std} | 布林带参数 |",
            f"| ma_short/mid/long | {cfg.ma_short}/{cfg.ma_mid}/{cfg.ma_long} | 均线参数 |",
            f"| min_market_cap_yi | {cfg.min_market_cap_yi} | 最小市值(亿) |",
            f"| max_market_cap_yi | {cfg.max_market_cap_yi} | 最大市值(亿) |",
            f"| max_pb | {cfg.max_pb} | 最大市净率 |",
            f"| benchmark_code | {cfg.benchmark_code} | 基准指数 |",
            "",
            "## 二、筛选股票",
            "",
            "| 排名 | 代码 | 名称 | 价格 | 涨跌% | 总分 | 信号 |",
            "|------|------|------|------|-------|------|------|",
        ]
        for i, s in enumerate(top_stocks, 1):
            sigs = " / ".join(sig.label for sig in s.signals) if s.signals else "-"
            lines.append(
                f"| {i} | {s.code} | {s.name} | {s.price:.2f} | {s.pct_change:+.2f} | {s.total_score} | {sigs} |"
            )

        lines += [
            "",
            "## 三、回测结果",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            f"| 总收益 | {result.total_return:+.2f}% |",
            f"| 年化收益 | {result.annual_return:+.2f}% |",
            f"| 夏普比率 | {result.sharpe_ratio:+.2f} |",
            f"| Sortino 比率 | {result.sortino_ratio:+.2f} |",
            f"| 最大回撤 | {result.max_drawdown:.2f}% |",
            f"| Calmar 比率 | {result.calmar_ratio:+.2f} |",
            f"| 胜率 | {result.win_rate:.1%} |",
            f"| 盈亏比 | {result.profit_factor:.2f} |",
            f"| 平均盈利 | {result.avg_win:+.2f}% |",
            f"| 平均亏损 | {result.avg_loss:+.2f}% |",
            f"| 交易次数 | {result.trade_count} |",
            f"| 平均持仓天数 | {result.avg_hold_days:.0f} |",
        ]
        if result.benchmark_return != 0.0:
            lines += [
                f"| 基准收益 | {result.benchmark_return:+.2f}% |",
                f"| 超额收益 | {result.excess_return:+.2f}% |",
            ]

        lines += [
            "",
            "## 四、交易明细",
            "",
            "| 代码 | 名称 | 买入日 | 卖出日 | 买入价 | 卖出价 | 收益% | 退出原因 | 持仓天数 |",
            "|------|------|--------|--------|--------|--------|-------|----------|----------|",
        ]
        for t in result.trades:
            rc = "+" if t.return_pct >= 0 else ""
            lines.append(
                f"| {t.code} | {t.name} | {t.entry_date} | {t.exit_date} | "
                f"{t.entry_price:.2f} | {t.exit_price:.2f} | {rc}{t.return_pct:.2f} | "
                f"{t.exit_reason} | {t.hold_days} |"
            )

        # 按退出原因统计
        reasons: dict[str, list] = {}
        for t in result.trades:
            reasons.setdefault(t.exit_reason, []).append(t.return_pct)
        lines += [
            "",
            "## 五、退出原因统计",
            "",
            "| 退出原因 | 次数 | 平均收益% | 胜率 |",
            "|----------|------|-----------|------|",
        ]
        for reason, rets in sorted(reasons.items()):
            avg = sum(rets) / len(rets)
            wr = sum(1 for r in rets if r > 0) / len(rets)
            lines.append(f"| {reason} | {len(rets)} | {avg:+.2f} | {wr:.0%} |")

        # 参数调优建议
        lines += [
            "",
            "## 六、参数调优建议",
            "",
            "| 当前值 | 建议方向 | 原因 |",
            "|--------|----------|------|",
        ]
        if result.win_rate < 0.45:
            lines.append(
                f"| 胜率 {result.win_rate:.0%} | 提高入场阈值 | 胜率偏低，考虑提高 entry_threshold |"
            )
        if result.max_drawdown > 15:
            lines.append(
                f"| 回撤 {result.max_drawdown:.1f}% | 收紧止损 | 回撤偏大，考虑降低 stop_loss_pct |"
            )
        if result.profit_factor < 1.5:
            lines.append(
                f"| 盈亏比 {result.profit_factor:.2f} | 调整止盈/止损 | 盈亏比偏低，考虑扩大 take_profit 或收紧 stop_loss |"
            )
        if result.sortino_ratio < 1.0:
            lines.append(
                f"| Sortino {result.sortino_ratio:.2f} | 减少下行风险 | Sortino 偏低，下行波动较大 |"
            )
        if result.win_rate >= 0.55 and result.profit_factor >= 2.0:
            lines.append("| - | 参数较优 | 胜率>55%且盈亏比>2.0，当前参数组合表现良好 |")

        lines += [
            "",
            "## 七、图表",
            "",
            "- 权益曲线: `output/equity_curve.png`",
            "- 回撤图: `output/drawdown.png`",
            "- 月度收益: `output/monthly_returns.png`",
            "",
            f"---\n\n*报告生成时间: {now}*",
        ]

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return filepath
