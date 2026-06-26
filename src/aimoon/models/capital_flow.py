"""Capital flow model — 主力资金, 北向, 龙虎榜."""

from pydantic import BaseModel


class CapitalFlowData(BaseModel):
    """Market capital flow (主力资金/北向/龙虎榜)."""

    symbol: str = ""

    # 主力资金（近5日累计）
    main_net_5d: float = 0.0  # 近5日主力净流入（元）

    # 多周期净流入（元）
    net_3d: float = 0.0
    net_10d: float = 0.0
    net_20d: float = 0.0

    # 北向资金
    northbound_chg: float = 0.0
    northbound_net_flow: float = 0.0
    northbound_hold_shares: float = 0.0
    northbound_hold_value: float = 0.0
    northbound_hold_ratio: float = 0.0
    northbound_date: str = ""

    # 龙虎榜（最近一次上榜，可空）
    lhb_date: str = ""
    lhb_reason: str = ""
    lhb_net_buy: float = 0.0  # 净买入（元）

    source: str = ""
