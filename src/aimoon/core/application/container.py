"""DI 容器 — 手写注册式容器，无反射无魔法。

通用容器不硬编码任何适配器类型；driving 层负责注册工厂函数。
core 层保持对 adapters 零依赖（六边形架构依赖规则）。

用法::

    container = Container()
    container.register(MyService, lambda: MyService(dep=...))
    svc = container.resolve(MyService)

    # 测试时替换
    container.override(MyService, MockService())
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class Container:
    """手动 DI 容器 — 无魔法，纯 Python。

    - ``register(cls, factory)`` 注册类型的工厂函数（由 driving 层调用）
    - ``resolve(cls)`` 解析依赖，自动管理单例
    - ``override(cls, instance)`` 测试时替换实现
    - ``reset()`` 清除单例和覆盖（保留工厂注册）
    """

    def __init__(self) -> None:
        self._singletons: dict[type, object] = {}
        self._factories: dict[type, Callable[[], object]] = {}
        self._overrides: dict[type, object] = {}

    def register(self, cls: type[T], factory: Callable[[], T]) -> None:
        """注册一个类型的工厂函数。"""
        self._factories[cls] = factory

    def resolve(self, cls: type[T]) -> T:
        """解析依赖，自动管理单例。测试时可用 override() 替换。"""
        if cls in self._overrides:
            return self._overrides[cls]  # type: ignore[return-value]
        if cls not in self._singletons:
            if cls not in self._factories:
                raise KeyError(f"No factory registered for {cls.__name__}")
            self._singletons[cls] = self._factories[cls]()
        return self._singletons[cls]  # type: ignore[return-value]

    def override(self, cls: type, instance: object) -> None:
        """测试时替换实现。"""
        self._overrides[cls] = instance
        self._singletons.pop(cls, None)

    def reset(self) -> None:
        """清除所有单例和覆盖（保留工厂注册）。"""
        self._singletons.clear()
        self._overrides.clear()
