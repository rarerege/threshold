"""Injectable clocks so every rule is testable with exact, repeatable times."""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def now_ms(self) -> int: ...


class SystemClock:
    def now_ms(self) -> int:
        return int(time.time() * 1000)


class FakeClock:
    def __init__(self, start_ms: int):
        self.t = start_ms

    def now_ms(self) -> int:
        return self.t

    def advance(self, *, minutes: float = 0, seconds: float = 0) -> None:
        self.t += int(minutes * 60_000 + seconds * 1000)
