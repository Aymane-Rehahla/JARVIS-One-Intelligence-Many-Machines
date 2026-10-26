# 0002: Two llama.cpp servers

Status: superseded by 0013 (26 Oct 2026).

## Context
The RX 580 has 8 GB of VRAM (`llama-server --list-devices`, 23 Oct: 8192 MiB, 7367 MiB free). One node speaks with
Qwen 3.5 2B roleplay (Q8_0, 2.08 GB); three silent nodes use Qwen3-0.6B (Q8_0, 0.64 GB). One server per node
would load the 0.6B weights three times.

## Decision
- **llama-server 1:** Qwen 3.5 2B roleplay, 1 slot, for incubator-1 (port 8081).
- **llama-server 2:** Qwen3-0.6B loaded once with 3 parallel slots (`-np 3`), one per silent node, each with its own
  KV cache (port 8082).
- All layers on the GPU (`-ngl 99`), few CPU threads, below-normal priority while the training runs.

## Alternatives
- **Four servers:** the 0.6B weights in VRAM three times.
- **One server for both models:** more coupling, and one crash silences all four.
- **CPU inference:** slower, and it competes with the training for the CPU.

## Consequences
- The weights take about 2.7 GB plus the KV caches. It should fit in 8 GB with room to spare; to be measured in 6A.
- A crash of server 2 silences three nodes at once. That needs fallback answers (A9) and a supervisor restart (Q6).
