"""K线柱值对象。

KlineBar 是一个值对象，概念上不可变。
一根K线没有独立的唯一标识，它通过日期和所属股票共同确定，
在领域模型中作为 KlineData 实体的组成部分存在。
"""

import math

from pydantic import BaseModel, model_validator


class KlineBar(BaseModel):
    """单根K线（OHLCV）数据。"""

    model_config = {"frozen": True}

    date: str
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    amount: float = 0.0
    pct_change: float | None = None

    @model_validator(mode="after")
    def _validate_business_rules(self) -> "KlineBar":
        # NaN 防御: NaN 的所有比较均为 False, 会绕过下方全部校验并污染技术指标。
        # 必须显式拦截, 由上游逐根 try/except 跳过该根 (2026-07-14)。
        for _f in (self.open, self.high, self.low, self.close, self.volume, self.amount):
            if math.isnan(_f) or math.isinf(_f):
                raise ValueError(f"K线含 NaN/Inf 非法数值: {_f}")
        if self.volume < 0:
            raise ValueError(f"volume 不能为负数: {self.volume}")
        if self.amount < 0:
            raise ValueError(f"amount 不能为负数: {self.amount}")
        if self.open <= 0 and self.high <= 0 and self.low <= 0 and self.close <= 0:
            raise ValueError("K线价格不能全为0")
        if self.high < self.open:
            raise ValueError(f"high ({self.high}) 不能小于 open ({self.open})")
        if self.high < self.close:
            raise ValueError(f"high ({self.high}) 不能小于 close ({self.close})")
        if self.high < self.low:
            raise ValueError(f"high ({self.high}) 不能小于 low ({self.low})")
        if self.low > self.open:
            raise ValueError(f"low ({self.low}) 不能大于 open ({self.open})")
        if self.low > self.close:
            raise ValueError(f"low ({self.low}) 不能大于 close ({self.close})")
        return self
