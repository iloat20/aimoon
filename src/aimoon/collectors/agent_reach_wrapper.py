"""Agent Reach wrapper — delegates platform data collection to
Agent Reach's upstream CLI tools.

Agent Reach: https://github.com/Panniantong/Agent-Reach
Installed tools: opencli, twitter, bili, gh, yt-dlp, etc.

This module provides a unified interface to call Agent Reach's upstream tools
for social media data collection, with graceful fallback to built-in collectors.
"""

from __future__ import annotations

import json
import subprocess

from ..models.social import SocialPost


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
        try:
            from agent_reach.channels.xueqiu import XueqiuChannel

            ch = XueqiuChannel()
            status, _ = ch.check()
            return status == "ok"
        except ImportError:
            return False

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
        """Fetch Xueqiu hot posts via Agent Reach Python API.

        Returns all hot posts without filtering.
        """
        try:
            from agent_reach.channels.xueqiu import XueqiuChannel

            ch = XueqiuChannel()
            status, _ = ch.check()
            if status != "ok":
                return []

            hot_posts = ch.get_hot_posts(20)
            posts: list[SocialPost] = []

            for item in hot_posts:
                title = item.get("title", "")
                text = item.get("text", "")
                posts.append(
                    SocialPost(
                        platform="雪球(AgentReach)",
                        title=title[:80] if title else text[:80] or "(无内容)",
                        content=text,
                        url=str(item.get("url", "")),
                        author=str(item.get("author", "")),
                        likes=int(item.get("likes", 0)),
                    )
                )

            return posts[:20]
        except ImportError:
            return []
        except Exception:
            return []

    @classmethod
    def fetch_toutiao(cls, keyword: str) -> list[SocialPost]:
        """Fetch news via Agent Reach (uses web/Jina as fallback)."""
        return []  # Agent Reach has no dedicated toutiao tool
