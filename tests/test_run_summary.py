"""render_run_summary 的单元测试。"""

from aimoon.adapters.driving.cli.run_summary import render_run_summary
from aimoon.core.domain.value_objects.collect_result import CollectResult


def test_renders_success_failure_empty():
    results = [
        CollectResult(platform="实时行情", status="success", count=1),
        CollectResult(platform="K线数据", status="success", count=244),
        CollectResult(platform="研报数据", status="failed", count=0, error="采集超时"),
        CollectResult(platform="资金流", status="empty", count=0),
        CollectResult(platform="社媒-股吧", status="timeout", count=0, error="连接重置"),
    ]
    out = render_run_summary(results, total_elapsed_ms=5300, skip_ai=False)

    assert "采集健康概览" in out
    assert "成功 2/5" in out
    assert "失败 1" in out
    assert "空 1" in out
    assert "其他 1" in out
    assert "实时行情" in out
    assert "研报数据" in out
    assert "采集超时" in out
    assert "总耗时 5.3s" in out


def test_skip_ai_footnote():
    results = [CollectResult(platform="实时行情", status="success", count=1)]
    out = render_run_summary(results, total_elapsed_ms=1200, skip_ai=True)
    assert "已跳过AI" in out


def test_empty_results():
    out = render_run_summary([], total_elapsed_ms=0, skip_ai=True)
    assert "无数据" in out
