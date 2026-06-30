"""社媒帖子实体。

SocialPost 是一个实体，以 url 作为唯一标识。
每篇社媒帖子/文章/视频通过 url 进行唯一区分。
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class SocialPost(BaseModel):
    """单条社媒帖子/文章/视频。"""

    platform: str
    url: str
    title: str = ""
    content: str = ""
    author: str = ""
    published_at: str = ""
    collected_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)  # noqa: UP017
    )

    likes: int = 0
    comments: int = 0
    shares: int = 0
    views: int = 0
