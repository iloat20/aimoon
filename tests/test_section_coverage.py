import sys
from pathlib import Path

import pytest

# 把 src 加进路径,方便在 uv 环境下直接 import
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from aimoon.adapters.driven.ai.pipeline import section_coverage as sc


def _full_report() -> str:
    """一段覆盖八个章节的最低示范文本。"""
    sections = [
        "## 一、业务画像与护城河\n\n核心业务结构分析,护城河来源论证。",
        "## 二、财务健康诊断\n\n成长性,ROE 杜邦拆解,现金流质量衡量。",
        "## 三、交叉验证\n\n业务 vs 财务背离检查,舆情与资金验证。",
        "## 四、风险量化与看空\n\n三条看空,每条含触发条件、冲击量级、概率评估。",
        "## 五、估值建模\n\nPE/PB 对比,FCFE 三档(保守档/中性档/乐观档)。",
        "## 六、逆向视角\n\n看多逻辑,市场共识错在哪,安全边际。",
        "## 七、投资建议\n\n评级与目标价格区间,止损,仓位,催化剂。",
        "## 八、附录\n\n财务时序表,同行竞品对比表,估值三档表。",
    ]
    return "\n\n".join(sections)


def test_full_coverage_all_eight():
    cov = sc.evaluate_coverage(_full_report())
    assert cov.all_present, f"missing: {cov.missing}"
    assert cov.hit == 8


def test_partial_coverage_detects_missing():
    # 只保留业务 + 财务两节
    partial = "\n\n".join([
        "## 一、业务画像与护城河\n\n护城河。",
        "## 二、财务健康诊断\n\nROE 杜邦。",
    ])
    cov = sc.evaluate_coverage(partial)
    assert cov.hit == 2
    assert cov.missing[0].startswith("三、")
    assert cov.ratio == 0.25


def test_coverage_ge_threshold():
    assert sc.coverage_ge(_full_report(), n=7) is True
    assert sc.coverage_ge(_full_report(), n=9) is False


def test_matches_by_section_number_not_just_heading():
    # 正文中出现 "一、业务画像" 这种无 ## 的写法也应识别
    text = "一、业务画像与护城河: 这里讨论了护城河与供应链。\n"
    text += "二、财务健康诊断: ROE杜邦与现金流。\n"
    text += "三、交叉验证: 业务财务背离。\n"
    text += "四、风险量化与看空: 触发条件与冲击。\n"
    text += "五、估值建模: FCFE 保守档中性档乐观档。\n"
    text += "六、逆向视角: 安全边际。\n"
    text += "七、投资建议: 目标价格区间与止损。\n"
    text += "八、附录: 财务时序表同行竞品对比表估值三档表。\n"
    cov = sc.evaluate_coverage(text)
    assert cov.hit == 8, f"missing: {cov.missing}"


def test_whitespace_normalization_matters():
    # ROE 杜邦 与 ROE杜邦 应等价
    text = "## 二、财务健康诊断\n\n这里做 ROE杜邦拆解。"
    cov = sc.evaluate_coverage(text)
    assert cov.per_section["二、财务健康诊断"] is True


def test_empty_report_zero_coverage():
    cov = sc.evaluate_coverage("")
    assert cov.hit == 0
    assert cov.total == 8


def test_count_headings_basic():
    md = "## 一、业务画像与护城河\n\n内容\n\n### subsection\n\n## 二、财务健康诊断\n"
    counts = sc.count_headings(md)
    assert counts.get("一、业务画像与护城河") == 1
    assert counts.get("二、财务健康诊断") == 1
