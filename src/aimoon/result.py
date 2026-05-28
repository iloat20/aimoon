"""结果类型，用于错误处理"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")
E = TypeVar("E", bound=str)


@dataclass(frozen=True, slots=True)
class Ok[T]:
    value: T

    def is_ok(self) -> bool:
        return True

    def is_err(self) -> bool:
        return False

    def unwrap(self) -> T:
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


type Result[T, E] = Ok[T] | Err[E]
