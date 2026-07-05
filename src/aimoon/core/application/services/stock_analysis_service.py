"""股票分析应用服务。

用函数式风格编排股票分析的完整流程，
所有外部依赖通过参数显式注入。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from aimoon.core.application.ports import AIAnalyzer, DataValidator, ReportGenerator
from aimoon.core.domain import (
    AnalysisReport,
    StockAnalysis,
)
from aimoon.core.domain.repositories import StockAnalysisRepository


async def collect_and_analyze(
    symbol: str,
    name: str,
    repo: StockAnalysisRepository,
    ai_analyzer: AIAnalyzer,
    data_validator: DataValidator,
    report_generator: ReportGenerator,
    output_dir: str | None = None,
    skip_ai: bool = False,
    *,
    use_pipeline_v2: bool = False,
) -> Path:
    """完整流水线：采集 → 验证 → AI分析 → 生成报告。

    Args:
        symbol: 6位股票代码
        name: 股票名称
        repo: 股票分析资源库
        ai_analyzer: AI分析器
        data_validator: 数据验证器
        report_generator: 报告生成器
        output_dir: 输出目录，可选
        skip_ai: 是否跳过AI分析
        use_pipeline_v2: 启用 AI pipeline v2 五阶段分析

    Returns:
        生成的报告文件路径
    """
    t0 = time.monotonic()
    logging.info("采集股票数据: %s %s", symbol, name)
    stock_analysis = await repo.collect_all(symbol, name)
    collect_results = await repo.get_collect_results()

    logging.info("数据质量校验:")
    data_warnings, data_confidence = _validate_data(stock_analysis, data_validator)

    if skip_ai:
        logging.info("跳过AI分析，使用基础数据汇总。")
        analysis = _fallback_analysis(stock_analysis)
    else:
        logging.info("AI分析中...")
        analysis = await _run_ai_analysis(
            stock_analysis, ai_analyzer, use_pipeline_v2=use_pipeline_v2
        )

    analysis = analysis.model_copy(
        update={
            "data_warnings": data_warnings,
            "data_confidence": data_confidence,
        }
    )

    logging.info("生成报告...")
    try:
        report_path = report_generator.generate(
            stock_analysis, analysis, collect_results, output_dir
        )
    except Exception as e:
        raise RuntimeError(f"生成报告失败: {type(e).__name__}: {e}") from e
    elapsed = int((time.monotonic() - t0) * 1000)
    logging.info("报告已生成: %s (总耗时 %dms)", report_path, elapsed)

    return report_path


async def analyze_stock(
    stock_analysis: StockAnalysis,
    ai_analyzer: AIAnalyzer,
    data_validator: DataValidator,
    *,
    skip_ai: bool = False,
    use_pipeline_v2: bool = False,
) -> AnalysisReport:
    """仅分析：验证 → AI分析。

    用于已有数据的重新分析。

    Args:
        stock_analysis: StockAnalysis 聚合根
        ai_analyzer: AI分析器
        data_validator: 数据验证器
        skip_ai: 是否跳过AI分析

    Returns:
        AnalysisReport 分析报告
    """
    logging.info("数据质量校验:")
    data_warnings, data_confidence = _validate_data(stock_analysis, data_validator)

    if skip_ai:
        logging.info("跳过AI分析，使用基础数据汇总。")
        analysis = _fallback_analysis(stock_analysis)
    else:
        logging.info("AI分析中...")
        analysis = await _run_ai_analysis(
            stock_analysis, ai_analyzer, use_pipeline_v2=use_pipeline_v2
        )

    analysis = analysis.model_copy(
        update={
            "data_warnings": data_warnings,
            "data_confidence": data_confidence,
        }
    )

    return analysis


def _validate_data(
    stock_analysis: StockAnalysis, data_validator: DataValidator
) -> tuple[list[str], dict[str, str]]:
    """验证数据质量并打印结果。"""
    try:
        warnings, confidence = data_validator.validate(stock_analysis)
    except Exception as e:
        logging.warning("[data_validate] %s: %s", type(e).__name__, e)
        warnings = []
        confidence = {
            "行情": "低",
            "K线数据": "低",
            "资金面": "低",
            "基本面": "低",
            "舆情数据": "低",
        }
    if warnings:
        for w in warnings:
            logging.info("   ⚠ %s", w)
    else:
        logging.info("   通过")
    return warnings, confidence


async def _run_ai_analysis(
    stock_analysis: StockAnalysis, ai_analyzer: AIAnalyzer, *, use_pipeline_v2: bool = False
) -> AnalysisReport:
    """执行AI分析，失败时返回降级结果。"""
    try:
        analysis = await ai_analyzer.analyze(stock_analysis, use_pipeline_v2=use_pipeline_v2)
    except Exception as e:
        logging.warning("[ai_analyze_stock] %s: %s", type(e).__name__, e)
        analysis = _fallback_analysis(stock_analysis)
    return analysis


def _fallback_analysis(stock_analysis: StockAnalysis) -> AnalysisReport:
    """AI分析不可用时的降级分析报告。"""
    return AnalysisReport(
        symbol=stock_analysis.symbol,
        name=stock_analysis.name,
        summary="AI分析暂不可用，以下为基础数据汇总。",
        report_text="AI分析暂不可用，以下为基础数据汇总。",
        investment_advice=("本报告由AI自动生成，仅供参考，不构成投资建议。"),
    )
