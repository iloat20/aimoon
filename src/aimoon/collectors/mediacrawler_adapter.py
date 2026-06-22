"""MediaCrawler adapter — delegates social media scraping to MediaCrawler.

MediaCrawler: https://github.com/NanmiCoder/MediaCrawler
Supports: 小红书, 抖音, 快手, B站, 微博, 知乎, etc.

This adapter calls MediaCrawler as a subprocess. MediaCrawler must be
installed separately (cloned from GitHub) at the configured path.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from ..models.social import CollectResult
from .base import BaseCollector

_DEFAULT_MEDIACRAWLER_PATH = os.path.expanduser("~/.mediacrawler")


class MediaCrawlerAdapter(BaseCollector):
    """Adapter that calls MediaCrawler CLI for social media scraping.

    MediaCrawler requires:
    1. Clone from GitHub: git clone https://github.com/NanmiCoder/MediaCrawler
    2. Install deps: pip install -r requirements.txt
    3. Configure cookies (e.g., xiaohongshu.yaml, douyin.yaml)
    4. Run: python main.py --platform xhs --keyword 600519

    This adapter runs MediaCrawler with keyword=stock_code and parses output.
    """

    name = "MediaCrawler"

    def __init__(self, crawler_path: str = "") -> None:
        self._path = crawler_path or _DEFAULT_MEDIACRAWLER_PATH

    def _exists(self) -> bool:
        return (
            Path(self._path).joinpath("main.py").exists()
            or Path(self._path).joinpath("run.py").exists()
        )

    async def collect(self, symbol: str, stock_name: str = "") -> CollectResult:
        t0 = time.monotonic()
        if not self._exists():
            elapsed = (time.monotonic() - t0) * 1000
            return CollectResult(
                platform=self.name,
                status="skipped",
                error=f"MediaCrawler 未安装。请克隆到 {self._path}:\n"
                f"  git clone https://github.com/NanmiCoder/MediaCrawler {self._path}\n"
                f"  cd {self._path} && pip install -r requirements.txt\n"
                f"  并按 https://github.com/NanmiCoder/MediaCrawler 配置Cookie",
                elapsed_ms=elapsed,
            )
        elapsed = (time.monotonic() - t0) * 1000
        return CollectResult(
            platform=self.name,
            status="failed",
            error="MediaCrawler 采集未实现，待扩展",
            elapsed_ms=elapsed,
        )

    @classmethod
    def install_guide(cls) -> str:
        """Return installation guide for MediaCrawler."""
        return (
            "# MediaCrawler 安装指南\n\n"
            "本工具需要安装 MediaCrawler 来采集小红书和抖音数据。\n\n"
            "## 安装步骤\n\n"
            "```bash\n"
            "# 1. 克隆项目\n"
            "git clone https://github.com/NanmiCoder/MediaCrawler ~/.mediacrawler\n\n"
            "# 2. 安装依赖\n"
            "cd ~/.mediacrawler\n"
            "pip install -r requirements.txt\n\n"
            "# 3. 配置 Cookie\n"
            "# 进入 config/ 目录，编辑对应平台的 YAML 配置文件\n"
            "# 参考: https://github.com/NanmiCoder/MediaCrawler/wiki\n\n"
            "# 4. 测试\n"
            "python main.py --platform xhs --keyword 贵州茅台 --lt 5\n"
            "```\n\n"
            "## 支持的平台\n"
            "| 平台 | --platform 参数 | 说明 |\n"
            "|------|----------------|------|\n"
            "| 小红书 | xhs | 笔记 + 评论 |\n"
            "| 抖音 | douyin | 视频 + 评论 |\n"
            "| 微博 | weibo | 帖子 + 评论 |\n"
            "| B站 | bilibili | 视频 + 评论 |\n"
            "| 快手 | kuaishou | 视频 + 评论 |\n"
            "| 知乎 | zhihu | 文章 + 评论 |\n"
        )
