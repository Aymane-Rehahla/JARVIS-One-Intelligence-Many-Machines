"""Simulated peer network between Jarvis nodes (ZigBee-like links).

Seeded latency and loss; a node can be cut off for a time window to test how the
others cope with a silent peer.
"""
from __future__ import annotations

import random


class PeerBus:
    def __init__(self, seed, *, latency_ticks: int = 1, drop_probability: float = 0.0):
        if latency_ticks < 0 or not 0.0 <= drop_probability < 1.0:
            raise ValueError("invalid peer bus configuration")
        self.rng = random.Random(f"farm-peer-bus-{seed}")
        self.latency_ticks, self.drop_probability = int(latency_ticks), float(drop_probability)
        self.queue: list[tuple[int, int, str, object]] = []
        self.isolated: dict[str, tuple[int, int]] = {}
        self.sent = self.dropped = self.delivered = 0
        self._sequence = 0

    def isolate(self, node_id: str, from_tick: int, until_tick: int) -> None:
        self.isolated[node_id] = (int(from_tick), int(until_tick))

    def _cut(self, node_id, tick):
        window = self.isolated.get(node_id)
        return window is not None and window[0] <= tick < window[1]

    def send(self, item, sender: str, targets, tick: int) -> None:
        for target in targets:
            self.sent += 1
            if self._cut(sender, tick) or self._cut(target, tick) or self.rng.random() < self.drop_probability:
                self.dropped += 1
                continue
            self._sequence += 1
            self.queue.append((tick + self.latency_ticks, self._sequence, target, item))

    def deliver(self, tick: int) -> dict[str, list]:
        due = sorted((entry for entry in self.queue if entry[0] <= tick), key=lambda e: (e[0], e[1]))
        self.queue = [entry for entry in self.queue if entry[0] > tick]
        inbox: dict[str, list] = {}
        for _, _, target, item in due:
            if self._cut(target, tick):
                self.dropped += 1
                continue
            inbox.setdefault(target, []).append(item)
            self.delivered += 1
        return inbox
