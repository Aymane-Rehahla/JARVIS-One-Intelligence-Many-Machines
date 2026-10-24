"""In-memory point-to-point device/Jarvis boundary; no Ethernet stack."""
from .simulated_io import SimulatedIO


class SimulatedRJ45Adapter:
    """Replaceable direct-link adapter using Matrix message envelopes."""

    def __init__(self, message_history_limit=128):
        self.io = SimulatedIO(message_history_limit=message_history_limit)

    @property
    def messages(self):
        return self.io.messages

    @property
    def delivery(self):
        return self.io.delivery

    def register(self, endpoint, handler):
        self.io.register(endpoint, handler)

    def send(self, message, now, delay=0):
        return self.io.send(message, now, delay)

    def deliver(self, now):
        return self.io.deliver(now)

    def reset(self):
        self.io.reset()
