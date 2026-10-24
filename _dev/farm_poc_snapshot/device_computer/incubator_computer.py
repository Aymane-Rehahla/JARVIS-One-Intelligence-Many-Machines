"""Incubator 1's simulated direct device-computer/Jarvis boundary.

The adapter is an in-memory RJ45/Ethernet-shaped seam, not a network stack.
"""
from ..fastcopy import json_copy

from ..transport.adapter import Message
from .device_computer import DeviceComputer


class IncubatorDeviceComputer(DeviceComputer):
    DEVICE_ID = "incubator-1"
    COMPUTER_ID = "incubator-1-computer"
    JARVIS_ID = "jarvis-real"

    def __init__(self, engine, adapter):
        super().__init__(engine, self.DEVICE_ID, adapter, direct=True)
        self.computer_id = self.COMPUTER_ID
        self.latest_observation = None
        self._reported_command_states = {}
        adapter.register(self.COMPUTER_ID, self._receive)

    def observation(self, *, share_history=False):
        payload = self.package()
        device = self.engine.world.devices[self.DEVICE_ID]
        payload["local_controller"] = {
            "power_status": device.internal["power_status"],
            "protection_active": bool(device.internal["cutoff"]),
            "heater_enabled": bool(device.internal["heater_on"]),
        }
        payload["recent_history"] = [
            json_copy(event) for event in self.engine.world.events[-32:]
            if event["source_id"] == self.DEVICE_ID
            and self.engine.world.is_public_event(event)
            # Keep the operationally relevant transition; scenario lifecycle
            # annotations belong to the evaluator/control plane, not Jarvis.
            and event["type"] in {"fault", "protection_activated", "protection_reset", "command_state"}
        ]
        history_count=max(0,int(self.engine.config.get("jarvis_history_samples", 8)))
        samples = device.telemetry_history[-history_count:] if history_count else []
        # publish() copies the whole payload once in transport, so it can share history records here.
        payload["telemetry_history"] = list(samples) if share_history else [json_copy(item) for item in samples]
        return payload

    def publish(self):
        device = self.engine.world.devices[self.DEVICE_ID]
        if not device.online:
            return None  # Jarvis keeps its last observation; the link is silent
        device.mark_communication(self.engine.clock.time)
        device.record_telemetry(self.engine.clock.tick, self.engine.clock.time)
        payload = self.observation(share_history=True)
        history = payload.pop("telemetry_history")
        message = Message(
            source_id=self.COMPUTER_ID, target_id=self.JARVIS_ID,
            message_type="device_observation", timestamp=self.engine.clock.time,
            payload=payload,
            correlation_id=f"telemetry-{self.engine.world.episode_id}-{self.engine.clock.tick}",
            message_id=f"device-{self.DEVICE_ID}-{self.sequence:06d}",
        )
        self.sequence += 1
        self.adapter.send(message, self.engine.clock.time)
        # Transport copied the fresh part; stored history records are immutable,
        # so the delivered payload and the cache share them instead of re-copying eight samples.
        message.payload["telemetry_history"] = history
        payload["telemetry_history"] = list(history)
        self.latest_package = message.payload
        self.latest_observation = self.latest_package
        return payload

    def _receive(self, message):
        if message.message_type != "command_request" or message.source_id != self.JARVIS_ID:
            self.engine.emit("device_interface_message_rejected", message.target_id,
                             sender_id=message.source_id, message_type=message.message_type)
            return
        try:
            command = self.engine.submit_command(
                self.DEVICE_ID, message.payload["command_type"],
                message.payload.get("arguments", {}), source=self.JARVIS_ID,
                correlation_id=message.correlation_id,
            )
            self.engine.emit("device_interface_command_accepted", self.DEVICE_ID,
                             command_id=command["command_id"], correlation_id=message.correlation_id)
        except (KeyError, ValueError) as exc:
            self.engine.emit("device_interface_message_rejected", message.target_id,
                             sender_id=message.source_id, reason=str(exc))

    def publish_command_updates(self):
        for command_id, command in self.engine.world.commands.items():
            if command["source_id"] != self.JARVIS_ID:
                continue
            status = command["status"]
            if self._reported_command_states.get(command_id) == status:
                continue
            self._reported_command_states[command_id] = status
            self._send_command_status(command)

    def publish_command_event(self, command_id, status):
        command=self.engine.world.commands.get(command_id)
        if not command or command["source_id"]!=self.JARVIS_ID:
            return
        self._reported_command_states[command_id]=status
        self._send_command_status(command)

    def _send_command_status(self,command):
        command_id=command["command_id"]
        status=command["status"]
        message = Message(
                source_id=self.COMPUTER_ID, target_id=self.JARVIS_ID,
                message_type="command_status", timestamp=self.engine.clock.time,
                payload=json_copy(command), command_id=command_id,
                correlation_id=command_id,
                message_id=f"command-status-{command_id}-{status}",
            )
        self.adapter.send(message, self.engine.clock.time)
