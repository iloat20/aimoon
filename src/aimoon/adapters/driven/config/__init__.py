"""配置 (Config)

应用程序配置管理。

职责：
- 加载环境变量和配置文件
- 提供统一的配置访问接口
"""

from .settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]
