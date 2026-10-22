"""按用户限流。

落雪查分器有速率限制，群里几十个人同时刷会把密钥打到 429，
所以除了 HTTP 层的最小间隔，还要在业务层按用户做冷却。
"""

from __future__ import annotations

import time
from typing import Callable, MutableMapping

__all__ = ["Cooldown"]


class Cooldown:
    """简单的时间窗口冷却器。

    时间源可注入，便于测试（不想在测试里真的 sleep）。
    """

    def __init__(self, seconds: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.seconds = max(0.0, float(seconds))
        self._clock = clock
        self._last: MutableMapping[object, float] = {}

    def remaining(self, key: object) -> float:
        """还剩多少秒才能再次查询；``0`` 表示可以查。"""
        if self.seconds <= 0:
            return 0.0
        last = self._last.get(key)
        if last is None:
            return 0.0
        elapsed = self._clock() - last
        return max(0.0, self.seconds - elapsed)

    def check(self, key: object) -> float:
        """检查并**记录**一次访问。返回剩余冷却秒数（``0`` 表示放行）。"""
        wait = self.remaining(key)
        if wait > 0:
            return wait
        self._last[key] = self._clock()
        return 0.0

    def reset(self, key: object | None = None) -> None:
        if key is None:
            self._last.clear()
        else:
            self._last.pop(key, None)

    def __len__(self) -> int:
        return len(self._last)
