# launcher/: start, supervise and stop JARVIS

`ports.toml` is the single source of truth for localhost ports. The supervisor starts programs in dependency order,
writes their state, and restarts a failed child within its bounded restart policy.

| Program | Starts when | Readiness |
|---|---|---|
| `nats` | always | TCP on the NATS port |
| `llama-incubator-1`, `llama-incubator-2`, `llama-hvac`, `llama-animal-protection` | unless `--no-llm` | each `/health` answers 200 |
| `farm` | after NATS | Matrix API `/health` answers 200 |
| four node runtimes | after NATS, and their own LLM when enabled | process remains running |
| `gateway` | after NATS and farm | gateway `/health` answers 200 |
| `website` | only with `--website` | its root page answers 200 |

## In and out

`start_all.ps1` accepts `-NoLlm`, `-NoWebsite`, `-Ports <file>`, `-Logs <directory>`, `-Only <comma-list>` and
`-NoMonitor`. It starts the resource monitor unless `-NoMonitor` is present, then starts the supervisor without
holding the console open. `-Ports` and `-Logs` are passed to the supervisor; `-Only` limits its named programs.

`stop_all.ps1` accepts `-Logs <directory>`. It asks that supervisor to stop, waits for its jobs, and only then considers
the monitor. A monitor is stopped only when `logs/launcher.monitor.json` matches the live monitor PID record; an
independently started monitor is explicitly left running. If the supervisor does not stop, the monitor is left running
because its RAM guard is still needed.

The launcher writes `supervisor.pid`, `supervisor.status.json`, `supervisor.jsonl` and one `<program>.out` file per
child in the selected logs directory. The status file is the current machine-readable state; the JSON-lines log is the
failure trail. The resource monitor writes its own PID, ownership record and samples under the project `logs/` folder.

While training runs, use `-NoLlm`: no llama.cpp server may start. After training, every node owns one independent
localhost server (decision 0013).

## Tests

`tests/test_launcher_scripts.py` starts `nats`, `farm` and `gateway` on generated ports with `-NoLlm -NoMonitor`,
checks gateway health, exercises duplicate-start protection, stops the supervisor and proves that an existing monitor
was not changed. `tests/test_supervisor.py` checks ordering, dependencies, health checks and cleanup.
