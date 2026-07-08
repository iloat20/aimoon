"""Progress reporter abstraction — replaces scattered print() calls in collectors.

Protocol-based: collectors depend on this interface, not on concrete output.
Three implementations cover production (CLI), test-silent, and test-recording.
"""

from __future__ import annotations

from typing import Protocol


class ProgressReporter(Protocol):
    """进度报告接口 — 采集器和编排器依赖此协议。"""

    def report(self, message: str, *, level: str = "info") -> None:
        """报告一条消息。level: info/warning/success."""
        ...

    def progress(self, stage: str, *, current: int, total: int) -> None:
        """报告进度。stage 如 '采集行情'、'采集K线'."""
        ...


class CliProgressReporter:
    """生产环境 — 保持现有 print 行为。"""

    def report(self, message: str, *, level: str = "info") -> None:
        print(message)  # noqa: T201 — CLI 进度输出

    def progress(self, stage: str, *, current: int, total: int) -> None:
        print(f"  {stage} ({current}/{total})")  # noqa: T201


class NullProgressReporter:
    """测试环境 — 静默，丢弃所有输出。"""

    def report(self, message: str, *, level: str = "info") -> None:
        pass

    def progress(self, stage: str, *, current: int, total: int) -> None:
        pass


class RecordingProgressReporter:
    """测试环境 — 记录所有调用供断言。"""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []  # (level, message)
        self.progress_calls: list[tuple[str, int, int]] = []  # (stage, current, total)

    def report(self, message: str, *, level: str = "info") -> None:
        self.messages.append((level, message))

    def progress(self, stage: str, *, current: int, total: int) -> None:
        self.progress_calls.append((stage, current, total))
