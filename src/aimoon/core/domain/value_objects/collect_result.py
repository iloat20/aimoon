"""采集结果值对象。

CollectResult 是一个值对象，概念上不可变。
它表示单个采集器的执行结果，没有独立的唯一标识，
作为采集操作的返回值存在。
"""

from pydantic import BaseModel, Field

from aimoon.core.domain.entities.social import SocialPost


class CollectResult(BaseModel):
    """单个采集器的执行结果。"""

    model_config = {"frozen": True}

    platform: str
    status: str
    posts: list[SocialPost] = Field(default_factory=list)
    error: str = ""
    count: int = 0
    elapsed_ms: float = 0.0
