"""Data models for social media data."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SocialPost(BaseModel):
    """A single social media post / article / video."""

    platform: str  # 雪球/头条/微信/股吧
    title: str = ""
    content: str = ""
    url: str = ""
    author: str = ""
    published_at: str = ""
    collected_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    # Engagement metrics
    likes: int = 0
    comments: int = 0
    shares: int = 0
    views: int = 0




class CollectResult(BaseModel):
    """Result from a single collector."""

    platform: str
    status: str  # success / partial / failed / timeout / skipped
    posts: list[SocialPost] = Field(default_factory=list)
    error: str = ""
    count: int = 0
    elapsed_ms: float = 0.0
