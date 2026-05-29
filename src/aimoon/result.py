"""结果类型，用于错误处理"""
from __future__ import annotations

from dataclasses import dataclass


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

    def unwrap(self) -> T:
        raise RuntimeError(f"Called unwrap on Err: {self.error}")

    def unwrap_or_exit(self, msg: str = "") -> T:
        import sys
        print(f"[red]{msg or self.error}[/red]")
        sys.exit(1)


type Result[T, E] = Ok[T] | Err[E]
