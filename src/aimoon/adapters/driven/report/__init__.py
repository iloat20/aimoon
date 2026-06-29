"""报告生成 (Report)

HTML 报告生成器。

职责：
- 渲染 HTML 分析报告
- 管理报告模板
- 格式化报告数据
"""

from .generator import HtmlReportGenerator

__all__ = ["HtmlReportGenerator"]
