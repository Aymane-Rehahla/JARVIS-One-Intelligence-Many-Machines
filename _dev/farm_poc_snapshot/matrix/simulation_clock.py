"""Integer-tick clock with stable ordering for scheduled callbacks."""
from dataclasses import dataclass, field
import heapq
from math import isfinite
from typing import Any, Callable

@dataclass(order=True)
class ScheduledEvent:
    tick: int
    sequence: int
    callback: Callable[[], Any] = field(compare=False)
    name: str = field(default="event", compare=False)

class SimulationClock:
    def __init__(self, step_seconds: float = 1.0):
        if not isfinite(float(step_seconds)) or step_seconds <= 0: raise ValueError("step_seconds must be finite and positive")
        self.step_seconds, self.tick, self._sequence, self._queue = step_seconds, 0, 0, []
    @property
    def time(self) -> float: return self.tick * self.step_seconds
    def schedule(self, tick: int, callback: Callable[[], Any], name: str = "event") -> None:
        if tick < self.tick: raise ValueError("cannot schedule in the past")
        heapq.heappush(self._queue, ScheduledEvent(tick, self._sequence, callback, name)); self._sequence += 1
    def due(self):
        while self._queue and self._queue[0].tick <= self.tick: yield heapq.heappop(self._queue)
    def advance(self): self.tick += 1
    def reset(self): self.tick, self._sequence, self._queue = 0, 0, []
