"""通用工具 (Common)

基础设施层通用工具。

职责：
- 重试逻辑和错误处理
- 通用解析工具
- 跨适配器共享工具（timing / mock）— 避免 collectors↔ai 跨层依赖
"""

from .browser import browser_session
from .mock import mock_analysis_report
from .parsers import parse_chinese_count
from .retry import retry_on_connection, silent_failure
from .timing import logphase

__all__ = [
    "browser_session",
    "silent_failure",
    "retry_on_connection",
    "parse_chinese_count",
    "logphase",
    "mock_analysis_report",
]
