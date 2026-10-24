# interface/gateway/: the only door (steps 6A and 6W)

FastAPI on 127.0.0.1:8010. It subscribes to NATS and sends the website throttled snapshots (at most 5 per second) shaped
for the website's `FarmState`, and it forwards only these actions:

| Website action | Route | Goes to |
|---|---|---|
| start, pause, reset, speed | `POST /sim/{action}` | Matrix API |
| inject a demo fault | `POST /faults/{scenario_id}` | Matrix API, never NATS (ground rule 4) |
| chat message | WebSocket `/ws/chat` | `jarvis.incubator-1.chat` |
| acknowledge an alert | `POST /alerts/{id}/ack` + confirm token | the Matrix's owner-acknowledgement signal |
| LLM failure switches | `POST /mentor/llm-failures` | `mentor.llm_failures` |

Streams: `/ws/farm`, `/ws/chat`, `/ws/audit`, `/ws/voice`. **Tests:** contract test (pydantic and zod), 6A and 6W "done when".
