"""股票分析应用服务。

用函数式风格编排股票分析的完整流程，
所有外部依赖通过参数显式注入。
"""

from __future__ import annotations

import logging
from pathlib import Path

from aimoon.core.application.ports import AIAnalyzer, DataValidator, ReportGenerator
from aimoon.core.domain import (
    AnalysisReport,
    DimensionScore,
    StockAnalysis,
    calculate_total_score,
    capital_flow_score,
    fundamental_score,
    news_score,
)
from aimoon.core.domain.repositories import StockAnalysisRepository
from aimoon.core.domain.services.scoring import (
    WEIGHT_CAPITAL_FLOW,
    WEIGHT_FUNDAMENTAL,
    WEIGHT_NEWS,
)


async def collect_and_analyze(
    symbol: str,
    name: str,
    repo: StockAnalysisRepository,
    ai_analyzer: AIAnalyzer,
    data_validator: DataValidator,
    report_generator: ReportGenerator,
    output_dir: str | None = None,
    skip_ai: bool = False,
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

    Returns:
        生成的报告文件路径
    """
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
        analysis = await _run_ai_analysis(stock_analysis, ai_analyzer)

    analysis = _build_analysis(stock_analysis, analysis, data_warnings, data_confidence)

    logging.info("生成报告...")
    try:
        report_path = report_generator.generate(
            stock_analysis, analysis, collect_results, output_dir
        )
    except Exception as e:
        raise RuntimeError(f"生成报告失败: {type(e).__name__}: {e}") from e
    logging.info("报告已生成: %s", report_path)

    return report_path


async def analyze_stock(
    stock_analysis: StockAnalysis,
    ai_analyzer: AIAnalyzer,
    data_validator: DataValidator,
    *,
    skip_ai: bool = False,
) -> AnalysisReport:
    """仅分析：验证 → AI分析 → 计算维度评分。

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
        analysis = await _run_ai_analysis(stock_analysis, ai_analyzer)

    analysis = _build_analysis(stock_analysis, analysis, data_warnings, data_confidence)

    return analysis


def _build_analysis(
    stock_analysis: StockAnalysis,
    analysis: AnalysisReport,
    data_warnings: list[str],
    data_confidence: dict[str, str],
) -> AnalysisReport:
    """合并校验结果与维度评分，返回最终 AnalysisReport。"""
    analysis = analysis.model_copy(
        update={
            "data_warnings": data_warnings,
            "data_confidence": data_confidence,
        }
    )
    return _compute_dimension_scores(stock_analysis, analysis)


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
    stock_analysis: StockAnalysis, ai_analyzer: AIAnalyzer
) -> AnalysisReport:
    """执行AI分析，失败时返回降级结果。"""
    try:
        analysis = await ai_analyzer.analyze(stock_analysis)
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


def _compute_dimension_scores(
    stock_analysis: StockAnalysis, analysis: AnalysisReport
) -> AnalysisReport:
    """计算各维度评分，返回新的 AnalysisReport 对象。"""
    cap_score = 3
    cap_detail = "详见报告正文（资金面分析）。"
    main_force = "持平"
    if stock_analysis.capital_flow and stock_analysis.capital_flow.source != "all_failed":
        try:
            cap_score, cap_detail, main_force = capital_flow_score(stock_analysis.capital_flow)
        except Exception as e:
            logging.warning("[capital_flow_score_calc] %s: %s", type(e).__name__, e)
    turnover = stock_analysis.quote.turnover if stock_analysis.quote else 0.0
    if 0 < turnover < 0.1:
        cap_score = 3
        cap_detail = "今日交投清淡，主力资金无明显动向，呈观望态势"
        main_force = "持平"

    fund_score = 3
    fund_detail = "数据不足，使用默认评分"
    try:
        fund_score, fund_detail = fundamental_score(stock_analysis.financial)
    except Exception as e:
        logging.warning("[fundamental_score_calc] %s: %s", type(e).__name__, e)

    news_score_val = 3
    news_detail = "数据不足，使用默认评分"
    try:
        news_score_val, news_detail = news_score(stock_analysis.research)
    except Exception as e:
        logging.warning("[news_score_calc] %s: %s", type(e).__name__, e)

    result = analysis.model_copy(
        update={
            "fundamental": DimensionScore(
                name="基本面",
                score=fund_score,
                weight=WEIGHT_FUNDAMENTAL,
                analysis=fund_detail,
            ),
            "capital_flow": DimensionScore(
                name="资金面",
                score=cap_score,
                weight=WEIGHT_CAPITAL_FLOW,
                analysis=cap_detail,
            ),
            "news": DimensionScore(
                name="新闻舆情",
                score=news_score_val,
                weight=WEIGHT_NEWS,
                analysis=news_detail,
            ),
            "main_force": main_force,
        }
    )
    result = result.model_copy(update={"total_score": calculate_total_score(result)})
    return result
