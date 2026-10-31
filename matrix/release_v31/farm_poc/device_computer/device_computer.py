"""Software publication boundaries; no instruction-level or network emulation."""
from ..fastcopy import json_copy

from ..transport.adapter import Message


class DeviceComputer:
    def __init__(self, engine, device_id, adapter, *, direct=False):
        self.engine=engine; self.device_id=device_id; self.adapter=adapter; self.direct=direct
        self.computer_id=f"{device_id}-computer"
        self.target_id="jarvis-real" if direct else device_id
        self.latest_package=None; self.sequence=0

    def package(self):
        device=self.engine.world.devices[self.device_id]
        telemetry=device.public_telemetry()
        return {"contract_version":1,"device_id":self.device_id,"tick":self.engine.clock.tick,
                "telemetry":telemetry}

    def publish(self):
        device=self.engine.world.devices[self.device_id]
        if not device.online:
            return None  # an unpowered or browned-out device sends nothing
        device.mark_communication(self.engine.clock.time)
        device.record_telemetry(self.engine.clock.tick,self.engine.clock.time)
        payload=self.package()
        message=Message(source_id=self.computer_id,target_id=self.target_id,
            message_type="device_observation" if self.direct else "device_telemetry",
            timestamp=self.engine.clock.time,payload=payload,
            correlation_id=f"telemetry-{self.engine.world.episode_id}-{self.engine.clock.tick}",
            message_id=f"device-{self.device_id}-{self.sequence:06d}")
        self.sequence+=1
        self.adapter.send(message,self.engine.clock.time)
        # The transport made the isolated copy; the local cache shares it.
        self.latest_package=message.payload
        # The package was freshly allocated and its cached/transport versions
        # are already isolated. Returning a third full copy is redundant.
        return payload
