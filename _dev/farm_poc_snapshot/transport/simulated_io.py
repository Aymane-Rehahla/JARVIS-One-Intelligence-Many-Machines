"""Simulated transport boundary for Matrix messages (not ZigBee)."""
from ..fastcopy import json_copy
from .message_delivery import MessageDelivery
class SimulatedIO:
    def __init__(self, message_history_limit=128):
        self.delivery=MessageDelivery(); self.handlers={}; self.messages=[]
        self.message_history_limit=message_history_limit
    def register(self,endpoint,handler): self.handlers[endpoint]=handler
    def send(self,message,now,delay=0):
        # One isolated copy per send: the delivered message and the diagnostic
        # history share it (receivers copy what they keep; nothing mutates it).
        message.payload=json_copy(message.payload)
        self.messages.append({"message_id":message.message_id,"source_id":message.source_id,"target_id":message.target_id,"message_type":message.message_type,"timestamp":message.timestamp,"payload":message.payload,"correlation_id":message.correlation_id,"command_id":message.command_id})
        if self.message_history_limit is not None:
            if self.message_history_limit <= 0:
                self.messages.clear()
            elif len(self.messages)>self.message_history_limit:
                del self.messages[:-self.message_history_limit]
        return self.delivery.send(message,now,delay)
    def deliver(self,now):
        got=self.delivery.due(now)
        for msg in got:
            handler=self.handlers.get(msg.target_id)
            if handler: handler(msg)
        return got
    def reset(self): self.delivery.reset(); self.messages=[]
