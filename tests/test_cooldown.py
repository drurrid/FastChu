"""用户冷却测试。"""

from __future__ import annotations

from fastchu.services.cooldown import Cooldown


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_first_call_passes() -> None:
    clock = FakeClock()
    cooldown = Cooldown(5.0, clock=clock)
    assert cooldown.check("u1") == 0.0


def test_second_call_is_blocked() -> None:
    clock = FakeClock()
    cooldown = Cooldown(5.0, clock=clock)
    cooldown.check("u1")
    assert cooldown.check("u1") == 5.0

    clock.advance(2.0)
    assert cooldown.check("u1") == 3.0


def test_passes_after_window_expires() -> None:
    clock = FakeClock()
    cooldown = Cooldown(5.0, clock=clock)
    cooldown.check("u1")
    clock.advance(5.0)
    assert cooldown.check("u1") == 0.0


def test_keys_are_independent() -> None:
    clock = FakeClock()
    cooldown = Cooldown(5.0, clock=clock)
    cooldown.check("u1")
    assert cooldown.check("u2") == 0.0
    assert cooldown.check("u1") == 5.0


def test_zero_seconds_disables_cooldown() -> None:
    cooldown = Cooldown(0.0)
    assert cooldown.check("u1") == 0.0
    assert cooldown.check("u1") == 0.0


def test_blocked_call_does_not_extend_the_window() -> None:
    """被拦下的那次不该刷新计时，否则一直点就一直被拦。"""
    clock = FakeClock()
    cooldown = Cooldown(5.0, clock=clock)
    cooldown.check("u1")
    clock.advance(3.0)
    assert cooldown.check("u1") == 2.0
    clock.advance(2.0)
    assert cooldown.check("u1") == 0.0


def test_reset() -> None:
    clock = FakeClock()
    cooldown = Cooldown(5.0, clock=clock)
    cooldown.check("u1")
    cooldown.reset("u1")
    assert cooldown.check("u1") == 0.0

    cooldown.check("u1")
    cooldown.check("u2")
    cooldown.reset()
    assert len(cooldown) == 0
