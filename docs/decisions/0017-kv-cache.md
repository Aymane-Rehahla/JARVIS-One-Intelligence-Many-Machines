# 0017: The KV cache stays f16

Status: accepted (builder, 30 Oct 2026, step 6C; the rule was set in the P2 spec before measuring).

## Context
D3 (27 Oct) left about 0.5 GiB of the RX 580's 8 GiB free with all four llama.cpp servers loaded. A quantized KV
cache (`-ctk q8_0 -ctv q8_0`, which needs flash attention for the V cache) could free memory. The rule, fixed before
the measurement: adopt q8_0 if it frees at least 0.3 GiB and slows generation by at most 10 %; otherwise keep f16.

## Measurement (`tools/kv_cache_probe.py`, `logs/kv_cache_probe.json`, 30 Oct 2026)
All four servers started together with the supervisor's commands (below-normal priority, each under the RAM guard),
then each server's own generation speed over a 128-token reply (3 runs, median), and the 2B model's replies to the
first 10 persona test lines with seed 7.

| | f16 (flash attention auto) | q8_0 (flash attention on) |
|---|---|---|
| Dedicated VRAM after all four loaded | 6.524 GiB | 6.632 GiB |
| VRAM in use before loading (other programs) | 1.695 GiB | 1.348 GiB |
| incubator-1, Qwen 3.5 2B, tokens/s | 22.26 | 20.85 (−6.3 %) |
| each Qwen3-0.6B, tokens/s | 36.24 · 36.27 · 36.43 | 28.59 · 28.60 · 28.55 (−21 %) |
| 2B load time | 5.44 s | 3.88 s |
| Replies to the 10 persona lines | 7 of 10 identical to q8_0; 3 differ in wording only | |

- **Flash attention works on the RX 580 with this Vulkan build:** every q8_0 server loaded with `-fa on` and answered.
  (This was an open question in the hand-off README.)
- Measured against the baseline before loading, the four servers used about 4.83 GiB with f16 and 5.28 GiB with q8_0;
  either way q8_0 freed nothing measurable.

## Decision
Keep f16 (`KV_CACHE_TYPE = "f16"`, flash attention "auto", llama.cpp's default). The cache types and the flash
attention setting are explicit in `ServerSpec` and `server_command()`, so a later change is one constant.

## Alternatives
- q8_0: rejected by the rule (no VRAM freed, 0.6B generation 21 % slower).
- Shorter contexts for the three small servers: not needed, VRAM fits.

## Consequences
- With all four servers, about 1.4 GiB of VRAM stays free.
- An unrelated finding, for 6C: without tool data in its prompt, the 2B model invents readings and even writes fake
  "[TOOL RESULTS]" blocks (all 10 replies are in the log). The agent therefore never sends a sentence whose numbers
  are not in that turn's tool results (verify.py, sentence by sentence), and its prompt always carries the real data.
