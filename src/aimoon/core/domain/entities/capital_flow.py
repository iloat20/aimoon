"""资金流向数据实体。

CapitalFlowData 是一个实体，以股票代码 symbol 作为唯一标识。
每只股票的资金流向数据通过 symbol 进行区分。
"""

from pydantic import BaseModel


class CapitalFlowData(BaseModel):
    """市场资金流向（主力资金/北向/龙虎榜）。"""

    symbol: str = ""

    main_net_5d: float = 0.0
    main_net_3d: float = 0.0
    main_net_10d: float = 0.0
    main_net_20d: float = 0.0

    # 主力净流入是否真正取到(任一主源 pysnowball/akshare 实际返回数据)。
    # 与 source!="all_failed" 不同:北向持股成功也会让 source 非空,但主力净流入可能仍缺失。
    # 模板据此区分「真实为 0」与「数据缺失」,避免把缺失渲染成误导性的 0.00 亿。
    main_net_available: bool = False

    northbound_chg: float = 0.0
    northbound_hold_shares: float = 0.0
    northbound_hold_value: float = 0.0
    northbound_hold_ratio: float = 0.0
    northbound_date: str = ""

    lhb_date: str = ""
    lhb_reason: str = ""
    lhb_net_buy: float = 0.0

    source: str = ""
