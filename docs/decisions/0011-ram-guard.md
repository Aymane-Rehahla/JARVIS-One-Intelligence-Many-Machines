# 0011: The RAM guard while the training runs

Status: accepted (owner's instruction, plan v3, point 6).

## Context
The Phase 5 training uses most of the 16 GB of RAM. The resource monitor showed free RAM swinging between about
0.6 GB and 6.7 GB every few seconds, from the training alone. Ground rule 1 says: stop what you started when free RAM
falls below about 2 GB.

## Decision
- **Never more than one LLM server while the training runs:** Qwen3-0.6B only, for plumbing tests. The 2B server runs
  only in short tests, when free RAM is above 3 GB.
- **The resource monitor's RAM guard stops only programs we started that registered themselves** in `logs/guarded.json`:
  - the launcher, the LLM test helpers and the voice tests register;
  - a stop happens when free RAM stays below 2 GiB for 3 samples in a row (30 s), or the commit headroom falls below 1 GiB.
- Each entry records the process's creation time, so a reused process id is never stopped.
- The training's processes are never registered, so the guard can never touch them.
- **One test or build at a time** while the training runs. Parallel helpers may write code, docs and reviews.

## Alternatives
- **Stop on any single sample below 2 GB:** the training's own brief dips would stop everything every few seconds.
- **Stop by process name (`python.exe`):** could hit the training. Rejected outright.

## Consequences
- A sustained memory squeeze ends our test programs, never the training. The stop is logged as an "action" record.
- Free RAM *staying* below 2 GB is also a stop-and-report condition for the build itself (plan v3, run rules).
