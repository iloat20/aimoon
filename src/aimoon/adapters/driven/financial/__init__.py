"""财务工具 (Financial)

财务数据获取和处理。

职责：
- 财务报表数据获取（akshare/东方财富）
- 财务数据缓存
"""

from .akshare_adapter import AkshareFinancialAdapter

__all__ = [
    "AkshareFinancialAdapter",
]
