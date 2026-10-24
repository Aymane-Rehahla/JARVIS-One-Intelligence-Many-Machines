"""Deterministic in-memory delivery with optional configured delay and loss."""
import heapq
class MessageDelivery:
    def __init__(self): self.queue=[]; self.seq=0; self.drop_next=0; self.delay_by_type={}
    def send(self,message,now,delay=0):
        if self.drop_next: self.drop_next-=1; return False
        due=now+max(0,delay)+max(0,self.delay_by_type.get(message.message_type,0)); heapq.heappush(self.queue,(due,self.seq,message)); self.seq+=1; return True
    def due(self,now):
        out=[]
        while self.queue and self.queue[0][0]<=now: out.append(heapq.heappop(self.queue)[2])
        return out
    def reset(self): self.queue=[]; self.seq=0; self.drop_next=0
