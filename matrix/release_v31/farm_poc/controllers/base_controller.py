class BaseController:
    def __init__(self, device): self.device=device
    def step(self, engine): raise NotImplementedError
    def command(self, kind, arguments): raise ValueError(f"unsupported command: {kind}")
