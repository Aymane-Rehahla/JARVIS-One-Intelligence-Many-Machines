"""The async event bus inside one node (S22): named topics, bounded queues, and no slow reader can block a writer.

    bus = EventBus()
    readings = bus.subscribe("farm.status")
    bus.publish("farm.status", status)            # never blocks
    status = await readings.get(timeout_s=1.0)    # None on timeout

When a reader's queue is full, its oldest item is dropped (and counted) so that it always sees the latest state (Q4).
"""
from __future__ import annotations

import asyncio
from typing import Generic, TypeVar

T = TypeVar("T")
QUEUE_SIZE = 256


class Reader(Generic[T]):
    """One subscriber's bounded queue on one topic."""

    def __init__(self, topic: str, maxsize: int) -> None:
        self.topic = topic
        self.queue: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def put(self, item: T) -> None:
        if self.queue.full():
            self.queue.get_nowait()
            self.dropped += 1
        self.queue.put_nowait(item)

    async def get(self, *, timeout_s: float | None = None) -> T | None:
        """The next item, or None when nothing arrives within `timeout_s` (None waits forever)."""
        if timeout_s is None:
            return await self.queue.get()
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=timeout_s)
        except asyncio.TimeoutError:
            return None


class EventBus:
    """Publish/subscribe between the parts of one node, all in one asyncio loop."""

    def __init__(self, *, maxsize: int = QUEUE_SIZE) -> None:
        if maxsize < 1:
            raise ValueError("queues need room for at least one item")
        self._maxsize = maxsize
        self._readers: dict[str, list[Reader[object]]] = {}

    def subscribe(self, topic: str, *, maxsize: int | None = None) -> Reader[object]:
        """A new reader of `topic`."""
        reader: Reader[object] = Reader(topic, maxsize or self._maxsize)
        self._readers.setdefault(topic, []).append(reader)
        return reader

    def unsubscribe(self, reader: Reader[object]) -> None:
        """Stop delivering to `reader`."""
        readers = self._readers.get(reader.topic, [])
        if reader in readers:
            readers.remove(reader)

    def publish(self, topic: str, item: object) -> int:
        """Give `item` to every reader of `topic` without waiting; returns how many readers got it."""
        readers = self._readers.get(topic, [])
        for reader in readers:
            reader.put(item)
        return len(readers)
