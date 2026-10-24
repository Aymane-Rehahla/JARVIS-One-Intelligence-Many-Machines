"""Replaceable message boundary; this module implements no radio protocol."""
from dataclasses import dataclass,field
from typing import Any,Protocol
@dataclass
class Message:
    source_id:str; target_id:str; message_type:str; timestamp:float; payload:dict[str,Any]=field(default_factory=dict); command_id:str|None=None; correlation_id:str|None=None; message_id:str=""
class TransportAdapter(Protocol):
    def send(self,message:Message,delay:float=0.0)->None: ...
    def deliver_due(self,now:float)->list[Message]: ...
