# 0013: One llama.cpp server per node

Status: accepted (owner decision, 26 Oct 2026). Supersedes 0002.

## Context
The owner wants complete node independence, including each node's language model. A failure of one node's llama.cpp
server must not affect the other nodes.

## Decision
- incubator-1 has one Qwen 3.5 2B roleplay server on port 8081, with one slot.
- incubator-2, hvac and animal-protection each have their own Qwen3-0.6B server on ports 8082, 8083 and 8084,
  respectively, each with one slot.
- Every server listens on 127.0.0.1. The three small servers use the same read-only model file; model weights are
  never copied.

## Consequences
- The RX 580's 8 GB of VRAM holds about 4.0 GB of model weights: 2.08 GB plus three times 0.64 GB, rather than the
  prior 2.7 GB. The KV caches stay the same size either way, with per-server overhead added by this topology.
- A failed server affects only its owning node; the remaining node models continue to serve their nodes.
- D3 must measure the actual fit. If it does not fit, D3 first measures an 8-bit KV cache
  (`-ctk q8_0 -ctv q8_0`), then shorter contexts for the three small nodes. Those choices are made only from D3's
  measurements.
