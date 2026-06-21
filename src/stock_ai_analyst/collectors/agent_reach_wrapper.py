"""Agent Reach wrapper — delegates platform data collection to Agent Reach's upstream CLI tools.

Agent Reach: https://github.com/Panniantong/Agent-Reach
Installed tools: opencli, twitter, bili, gh, yt-dlp, etc.

This module provides a unified interface to call Agent Reach's upstream tools
for social media data collection, with graceful fallback to built-in collectors.
"""

from __future__ import annotations

import json
import subprocess
from typing import Optional

from ..models.social import CollectResult, SocialPost


def _run_tool(cmd: list[str], timeout: int = 30) -> tuple[str, str]:
    """Run a CLI tool and return (stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return "", f"tool not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return "", "timeout"
    except Exception as e:
        return "", str(e)


class AgentReachWrapper:
    """Wrapper for Agent Reach installed CLI tools."""

    @staticmethod
    def is_installed() -> bool:
        """Check if Agent Reach tooling is available."""
        stdout, _ = _run_tool(["which", "agent-reach"])
        return bool(stdout)

    @staticmethod
    def doctor() -> dict:
        """Run agent-reach doctor --json and parse output."""
        stdout, _ = _run_tool(["agent-reach", "doctor", "--json"], timeout=15)
        if not stdout:
            return {}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {}

    @classmethod
    def fetch_xueqiu_hot(cls, symbol: str, stock_name: str = "") -> list[SocialPost]:
        """Fetch Xueqiu hot posts via Agent Reach / opencli."""
        if not cls.is_installed():
            return []

        # Agent Reach uses `opencli xueqiu search` for Xueqiu
        stdout, stderr = _run_tool(
            ["opencli", "xueqiu", "search", f"{symbol} {stock_name}" if stock_name else symbol, "-n", "10", "-f", "json"],
            timeout=30,
        )
        if not stdout:
            return []

        posts: list[SocialPost] = []
        try:
            items = json.loads(stdout)
            if isinstance(items, list):
                for item in items[:10]:
                    posts.append(SocialPost(
                        platform="雪球(AgentReach)",
                        title=str(item.get("title", "")),
                        content=str(item.get("content", item.get("description", ""))),
                        url=str(item.get("url", "")),
                        author=str(item.get("author", "")),
                        published_at=str(item.get("created_at", "")),
                        likes=int(float(item.get("likes", 0) or 0)),
                        comments=int(float(item.get("comments", 0) or 0)),
                    ))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return posts

    @classmethod
    def fetch_xiaohongshu(cls, keyword: str) -> list[SocialPost]:
        """Fetch Xiaohongshu notes via Agent Reach / opencli."""
        if not cls.is_installed():
            return []

        stdout, stderr = _run_tool(
            ["opencli", "xiaohongshu", "search", keyword, "-n", "10", "-f", "json"],
            timeout=60,
        )
        if not stdout:
            return []

        posts: list[SocialPost] = []
        try:
            items = json.loads(stdout)
            if isinstance(items, list):
                for item in items[:10]:
                    posts.append(SocialPost(
                        platform="小红书(AgentReach)",
                        title=str(item.get("title", item.get("display_title", ""))),
                        content=str(item.get("desc", item.get("content", ""))),
                        url=str(item.get("url", item.get("share_url", ""))),
                        author=str(item.get("author", item.get("user", {}).get("nickname", ""))),
                        published_at=str(item.get("time", item.get("create_time", ""))),
                        likes=int(float(item.get("liked_count", 0) or 0)),
                        comments=int(float(item.get("comment_count", 0) or 0)),
                    ))
        except Exception:
            pass
        return posts

    @classmethod
    def fetch_toutiao(cls, keyword: str) -> list[SocialPost]:
        """Fetch news via Agent Reach (uses web/Jina as fallback)."""
        return []  # Agent Reach has no dedicated toutiao tool
