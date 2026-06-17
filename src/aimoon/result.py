"""结果类型，用于错误处理"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import NoReturn


def _rich_print(msg: str) -> None:
    """Rich 可用时使用 rich 输出，否则 fallback 到普通 print。"""
    try:
        from rich.console import Console

        Console().print(f"[red]{msg}[/red]")
    except ImportError:
        print(f"ERROR: {msg}")


@dataclass(frozen=True, slots=True)
class Ok[T]:
    value: T

    def is_ok(self) -> bool:
        return True

    def is_err(self) -> bool:
        return False

    def unwrap(self) -> T:
        return self.value

    def unwrap_or_exit(self, msg: str = "") -> T:
        return self.value


@dataclass(frozen=True, slots=True)
class Err[E]:
    error: E

    def is_ok(self) -> bool:
        return False

    def is_err(self) -> bool:
        return True

    def unwrap(self) -> NoReturn:
        raise RuntimeError(f"Called unwrap on Err: {self.error}")

    def unwrap_or_exit(self, msg: str = "") -> NoReturn:
        _rich_print(msg or str(self.error))
        sys.exit(1)


type Result[T, E] = Ok[T] | Err[E]
