"""P1 渲染层修正的确定性测试(#10 分季度同比空列 / #11 Peer 空列 / #12 分业务合并)。"""
from aimoon.adapters.driven.ai.pipeline.table_renderer import (
    render_peer_comparison,
    render_quarterly_breakdown,
    render_region_breakdown,
)
from aimoon.adapters.driven.financial.akshare_adapter import _merge_other_segments


def test_render_peer_hides_empty_columns():
    """同行 ROE/营收增速/净利增速 整列全空时自动隐藏(P1 #11)。

    peer_compare 未采集这些字段时返回 0.0(哨兵),须与 None 同样视为空。
    """
    data = {
        "peers": [
            {
                "name": "美的集团",
                "pe": 12.0,
                "pb": 2.5,
                "mcap": 5000.0,
                "price": 70.0,
                "roe": 0.0,
                "rev_g": 0.0,
                "np_g": 0.0,
            },
        ]
    }
    md = render_peer_comparison(data)
    assert "ROE(%)" not in md
    assert "营收增速(%)" not in md
    assert "净利增速(%)" not in md
    assert "美的集团" in md
    assert "PE" in md
    assert "PB" in md


def test_render_peer_keeps_columns_when_data_present():
    """若数据源补齐字段,对应列应自动出现。"""
    data = {
        "peers": [
            {
                "name": "A",
                "pe": 10.0,
                "pb": 1.0,
                "mcap": 100.0,
                "price": 5.0,
                "roe": 0.15,
                "rev_g": 0.1,
                "np_g": 0.2,
            },
        ]
    }
    md = render_peer_comparison(data)
    assert "ROE(%)" in md
    assert "营收增速(%)" in md
    assert "净利增速(%)" in md


def test_render_quarterly_drops_yoy_when_all_na():
    """四个季度同比全缺时隐藏同比列,仅留单季营收/净利(P1 #10)。"""

    class F:
        annual_report_footnotes = {
            "quarterly_breakdown": {
                "available": True,
                "quarters": [
                    {"quarter": "第一季度", "revenue_yi": 415.1, "net_profit_yi": 59.04},
                    {"quarter": "第二季度", "revenue_yi": 558.2, "net_profit_yi": 85.08},
                ],
            }
        }

    md = render_quarterly_breakdown(F())
    assert "营收同比" not in md
    assert "净利同比" not in md
    assert "第一季度" in md
    assert "415.1" in md
    assert "第二季度" in md


def test_render_quarterly_keeps_yoy_when_present():
    """存在任一同比值时保留同比列。"""

    class F:
        annual_report_footnotes = {
            "quarterly_breakdown": {
                "available": True,
                "quarters": [
                    {
                        "quarter": "第一季度",
                        "revenue_yi": 415.1,
                        "net_profit_yi": 59.04,
                        "revenue_yoy": 5.0,
                        "net_profit_yoy": -2.0,
                    },
                ],
            }
        }

    md = render_quarterly_breakdown(F())
    assert "营收同比" in md
    assert "5.0" in md


def test_render_segment_merges_other_rows():
    """按产品分类中多个「其他」类残差行合并为单一「其他业务」(P1 #12)。"""
    rows = [
        {"name": "空调", "revenue_yi": 1500.0, "ratio": 0.78, "gross_margin": 0.32},
        {"name": "其他(补充)", "revenue_yi": 200.0, "ratio": 0.1, "gross_margin": 0.2},
        {"name": "其他", "revenue_yi": 100.0, "ratio": 0.05, "gross_margin": 0.15},
        {"name": "智能装备", "revenue_yi": 50.0, "ratio": 0.03, "gross_margin": 0.1},
    ]
    merged = _merge_other_segments(rows)
    # 合并后只有一个「其他业务」行,且营收/占比为两者之和(300 亿 / 0.15)
    names = [r["name"] for r in merged]
    assert names.count("其他业务") == 1
    other = next(r for r in merged if r["name"] == "其他业务")
    assert other["revenue_yi"] == 300.0
    assert abs(other["ratio"] - 0.15) < 1e-6
    assert "其他(补充)" not in names
    assert "其他" not in names
    # 非「其他」行原样保留
    assert "空调" in names
    assert "智能装备" in names


def test_render_quarterly_reconcile_note_present():
    """P2 勾稽: 全年营收/净利可用时,单季加总 vs 全年合计数口径差脚注出现。"""
    # 全年营收/净利为元单位(与 FinancialData 一致)
    ann_rev = 171118161275.41
    ann_np = 28862746016.16

    class F:
        revenue = ann_rev
        net_profit = ann_np
        annual_report_footnotes = {
            "quarterly_breakdown": {
                "available": True,
                "quarters": [
                    {"quarter": "第一季度", "revenue_yi": 415.1, "net_profit_yi": 59.04,
                     "revenue_yoy": 14.1, "net_profit_yoy": 26.3},
                    {"quarter": "第二季度", "revenue_yi": 558.2, "net_profit_yi": 85.08,
                     "revenue_yoy": -12.0, "net_profit_yoy": -10.1},
                    {"quarter": "第三季度", "revenue_yi": 398.6, "net_profit_yi": 70.49,
                     "revenue_yoy": -15.1, "net_profit_yoy": -9.9},
                    {"quarter": "第四季度", "revenue_yi": 332.7, "net_profit_yi": 75.42,
                     "revenue_yoy": -21.6, "net_profit_yoy": -26.2},
                ],
            }
        }

    md = render_quarterly_breakdown(F())
    assert "单季营收加总" in md
    assert "与全年营收" in md
    assert "非数据错误" in md


def test_render_quarterly_no_reconcile_when_annual_missing():
    """P2 勾稽: 全年营收/净利均缺失(=0)时不显示口径差脚注(避免无意义 0/0)。"""

    class F:
        revenue = 0.0
        net_profit = 0.0
        annual_report_footnotes = {
            "quarterly_breakdown": {
                "available": True,
                "quarters": [
                    {"quarter": "第一季度", "revenue_yi": 415.1, "net_profit_yi": 59.04},
                ],
            }
        }

    md = render_quarterly_breakdown(F())
    assert "非数据错误" not in md


def test_render_region_residual_note_present():
    """P2 勾稽: 内销+外销占比合计 <100% 时,残差(其他业务/未分区)脚注出现。"""

    class F:
        annual_report_footnotes = {
            "region_breakdown": {
                "available": True,
                "regions": [
                    {"name": "内销", "revenue_yi": 1264.1, "ratio": 74.2,
                     "yoy": -10.7, "gross_margin": 34.5},
                    {"name": "外销", "revenue_yi": 273.8, "ratio": 16.1,
                     "yoy": -2.9, "gross_margin": 24.5},
                ],
            }
        }

    md = render_region_breakdown(F())
    assert "残差" in md
    assert "为其他业务/未分区收入" in md
    assert "未单列地区" in md


def test_render_region_no_residual_note_when_full():
    """P2 勾稽: 地区占比合计=100% 时无残差脚注。"""

    class F:
        annual_report_footnotes = {
            "region_breakdown": {
                "available": True,
                "regions": [
                    {"name": "内销", "revenue_yi": 600.0, "ratio": 60.0,
                     "yoy": -1.0, "gross_margin": 30.0},
                    {"name": "外销", "revenue_yi": 400.0, "ratio": 40.0,
                     "yoy": -1.0, "gross_margin": 20.0},
                ],
            }
        }

    md = render_region_breakdown(F())
    assert "未单列地区" not in md
