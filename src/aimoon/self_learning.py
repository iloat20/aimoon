"""SelfLearningManager - 自学习管理器。

管理后台自学习任务，提供错误传播和健康状态监控。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TaskHealth:
    """任务健康状态。"""

    task_name: str
    last_success: float = 0.0
    last_failure: float = 0.0
    consecutive_failures: int = 0
    total_successes: int = 0
    total_failures: int = 0
    last_error: str | None = None


class SelfLearningManager:
    """自学习管理器。

    管理后台自学习任务，提供错误传播和健康状态监控。
    """

    def __init__(self, max_consecutive_failures: int = 5):
        self.max_consecutive_failures = max_consecutive_failures
        self._health: dict[str, TaskHealth] = {}
        self._lock = threading.Lock()

    def execute_task(
        self,
        task_name: str,
        task_func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """执行任务并记录健康状态。

        Args:
            task_name: 任务名称
            task_func: 任务函数
            *args: 任务参数
            **kwargs: 任务关键字参数

        Returns:
            任务结果
        """
        with self._lock:
            if task_name not in self._health:
                self._health[task_name] = TaskHealth(task_name=task_name)

        try:
            result = task_func(*args, **kwargs)

            with self._lock:
                health = self._health[task_name]
                health.last_success = time.time()
                health.consecutive_failures = 0
                health.total_successes += 1
                health.last_error = None

            logger.debug("Task '%s' completed successfully", task_name)
            return result

        except Exception as e:
            with self._lock:
                health = self._health[task_name]
                health.last_failure = time.time()
                health.consecutive_failures += 1
                health.total_failures += 1
                health.last_error = str(e)

            logger.warning(
                "Task '%s' failed (consecutive: %d): %s",
                task_name,
                health.consecutive_failures,
                e,
            )

            # 检查是否需要告警
            if health.consecutive_failures >= self.max_consecutive_failures:
                logger.error(
                    "Task '%s' has failed %d consecutive times! Last error: %s",
                    task_name,
                    health.consecutive_failures,
                    health.last_error,
                )

            raise

    def get_health(self, task_name: str) -> TaskHealth | None:
        """获取任务健康状态。"""
        with self._lock:
            return self._health.get(task_name)

    def get_all_health(self) -> dict[str, TaskHealth]:
        """获取所有任务健康状态。"""
        with self._lock:
            return dict(self._health)

    def is_healthy(self, task_name: str) -> bool:
        """检查任务是否健康。"""
        with self._lock:
            health = self._health.get(task_name)
            if health is None:
                return True  # 未执行的任务视为健康
            return health.consecutive_failures < self.max_consecutive_failures

    def reset_task(self, task_name: str) -> None:
        """重置任务健康状态。"""
        with self._lock:
            if task_name in self._health:
                self._health[task_name] = TaskHealth(task_name=task_name)

    def summary(self) -> str:
        """生成健康状态摘要。"""
        with self._lock:
            if not self._health:
                return "No tasks monitored"

            lines = ["=== Self-Learning Health Summary ==="]
            for task_name, health in sorted(self._health.items()):
                status = (
                    "✅ Healthy"
                    if health.consecutive_failures < self.max_consecutive_failures
                    else "❌ Unhealthy"
                )
                lines.append(f"\n{task_name}:")
                lines.append(f"  Status: {status}")
                lines.append(f"  Successes: {health.total_successes}")
                lines.append(f"  Failures: {health.total_failures}")
                lines.append(f"  Consecutive Failures: {health.consecutive_failures}")
                if health.last_error:
                    lines.append(f"  Last Error: {health.last_error}")
                if health.last_success:
                    lines.append(
                        f"  Last Success: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(health.last_success))}"
                    )

            return "\n".join(lines)


# 全局实例
_self_learning_manager: SelfLearningManager | None = None


def get_self_learning_manager() -> SelfLearningManager:
    """获取全局自学习管理器。"""
    global _self_learning_manager
    if _self_learning_manager is None:
        _self_learning_manager = SelfLearningManager()
    return _self_learning_manager


def execute_self_learning_task(
    task_name: str,
    task_func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """执行自学习任务。"""
    manager = get_self_learning_manager()
    return manager.execute_task(task_name, task_func, *args, **kwargs)
