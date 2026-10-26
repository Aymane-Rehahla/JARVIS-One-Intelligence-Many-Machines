# launcher/: start and stop everything

| File | Step | What it does |
|---|---|---|
| `ports.toml` | 6.0 | every port, all on 127.0.0.1 |
| `start_all.ps1` | 6A | starts NATS, the LLM server(s), the farm runtime, the four nodes, the gateway and the website, in order, waiting for each health check; registers memory-heavy programs with the resource monitor's RAM guard |
| `stop_all.ps1` | 6A | stops them in reverse order |
| supervisor | 6A | restarts a crashed program and logs it (Q6) |

- **While the training runs:** no LLM server is started. After training, each node owns one independent local server
  (decision 0013).
- **Status (step 6.0):** only `ports.toml` exists.
- **Tests:** 6A "done when": killing a node gets it restarted by the supervisor.
