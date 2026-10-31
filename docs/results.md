# Results

Measured on the build PC (Ryzen 5 5600G, 16 GB RAM, Radeon RX 580 8 GB), in the simulated farm, on 31 Oct 2026.
These are simulation results, not field performance. 5 of the 10 checks met their target; the misses are reported
as measured. Each number comes from a script in `tests/llm/eval/`; the full generated report, with every raw value,
is `tests/llm/eval/report.md`.

## Summary

| # | What | Target | Measured | Met? |
|---|---|---|---|---|
| E1 | Numbers in answers that match the live data | 100 % | 44 of 44 (before the checker: 89 %) | yes |
| E2 | Citations that support what was said | 95 % | 3 of 4 | no |
| E3 | Manual search finds the right passage in the top 4 | 0.90 | 0.89 (hybrid beats keywords 0.85 and vectors 0.84) | no |
| E4 | Valid tool calls by the 2B model | 98 % | 46 of 46 (free-form JSON: 83 %) | yes |
| E5 | Questions routed to the right machine | 95 % | 88 % (60 of 68; ambiguous questions 56 %) | no |
| E6 | Speed | first sentence 1.5 s | first sentence 3.3 s; spoken reply 8.7 s; alert spoken 1.44 s | no |
| E7 | Graceful answers when a model fails or stalls | 100 % | 12 of 12, no invented numbers | yes |
| E8 | Safety under attack prompts | 0 unsafe | 0 unsafe tool calls, 0 actions, 0 memory access (18 attacks) | yes |
| E9 | Fault names leaking to JARVIS | 0 | 0 | yes |
| E10 | Faults caught end to end | all | 15 of 20 (see below) | no |

## Fault detection (E10)

Five runs per fault, on simulation seeds never used in training; 15 farm minutes after the fault.

| Fault | Trained CTM brain | Median time | Hand-written rules | Median time |
|---|---|---|---|---|
| Incubator heater relay stuck | 5 of 5 | 86 s | 5 of 5 | 76 s |
| HVAC supply fan failure | 5 of 5 | 171 s | 5 of 5 | 19 s |
| Power supply overvoltage | 5 of 5 | 32 s | 5 of 5 | 1 s |
| Humidity sensor slow drift | 0 of 5 | | 0 of 5 | |

The trained brains catch what the rules catch, more slowly; neither catches the slow drift within 15 minutes.

## Since the evaluation (31 Oct, live checks)

| Check | Result |
|---|---|
| Calm farm: owner alerts in 90 s at 60× with nothing injected | 0 (was 20) |
| A barn fire injected | alert to the owner within 45 s |
| Two questions at once on incubator-1 | 4.1 s together (one alone: 3.4 s) |
| Small talk ("how are you doing today?") | a friendly reply, first words in 1.8 s |
| Automated tests | 1 029 pass, 11 heavy ones skipped by default |

## What is weak, plainly

- Spoken replies take about 8 s on this GPU; the target was 2.5 s.
- The CTM is slower than simple rules on clear faults, and misses the slow sensor drift.
- Routing misses some ambiguous questions; the 2B model sometimes says something the data does not support when no
  number is involved (numbers are always checked).
- Everything is measured in simulation; real sensors are the next step.
