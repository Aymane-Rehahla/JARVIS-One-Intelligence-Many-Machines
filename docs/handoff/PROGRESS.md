# PROGRESS: the one builder's running log (Phase 6, 30 Oct - 1 Nov 2026)

Re-read this file and `docs/handoff/README.md` section 4 after any context summary, before continuing.
Never write the three outside paths (the release folder, the training's Python, the upstream CTM folder) here.

## Done (31 Oct, about 06:00)

| Step | Commits / tag | Evidence |
|---|---|---|
| Step 0: redesigned website | `7de674e` | WORKLOG "Step 0" |
| P1 (6W part 1) | tag `step-6W` | WORKLOG "Step 6W, part 1" |
| 6C: engines, agent, minds, alerts, failure switches, gateway chat routes, Matrix ack, prompt-cache warm-up | `b4c25b3` .. `44847ad` | WORKLOG "Step 6C, part 1"; decisions 0017-0019 |
| Website chat, confirm click, alert banner, LLM switches, JARVIS identity | `acad25a` | WORKLOG "Step 6W, part 2" |
| 6D: torch, install with proofs, equivalence (differs only by the renamed seeds), trained feed, real run, E9, E10 | `92090d1`, `8cdfb81`, `f5c9211` | WORKLOG "Step 6D, part 1"; decision 0021 |
| Evaluation scripts E1-E10, live rig, gate rules and documents fixes | `f5c9211` | logs/eval/*.json (results to WORKLOG) |
| 6E: voice box, VAD/STT/TTS, voice routes, website voice panel, D1 passes | `1bf6ed6`, `00f6c8b` | decision 0022 |
| Citations only when used; story and architecture docs | `bb917bb` | |
| Gate: machine actions only from the machines' data | `02dbee3` | |
| The 6C-6E review's 16 findings fixed (feed-bound ack, words not data, bounded turns, voice, alerts resent, evals) | `28e09cb` | WORKLOG "The independent review of 6C, 6D and 6E" |
| Verifying every reply found two bugs (a colon after a time; a regeneration repeating sentences); `-VoiceWav` | `e4d849b` | |

## In flight

- The evaluation reruns on the fixed code (E1/E2, E8 done at `28e09cb`; E7 9/12 found the colon bug, fixed in
  `e4d849b`; E6 text and voice running). Then E1/E2 and E7 again at `e4d849b`.

## Next

1. Fix the review's findings; E-results into WORKLOG; full suite twice + pyright; tags `step-6C`, `step-6D`, `step-6E`.
2. `run_all.py` -> `docs/results-phase6.md`.
3. 6F: final review against plan sections 2, 3, 8; three cold-start rehearsals (start_all with the LLM servers; the
   four faults from the website; a chat question; a push-to-talk question with `JARVIS_VOICE_WAV=logs\rehearsal_question.wav`;
   hot-join by killing a node program); docs; tag `step-6F`; final checks (suite twice, pyright, website build, the
   full name scan, nothing left running) and the owner's summary.

## Measured so far (details in WORKLOG and logs/eval)

E1 after the gate 27/27 (before 21/23). E2 3/12 before the citation fix. E3 hybrid R@4 0.891 after the documents
fixes (was 0.909; one question). E5 60/68 = 88.2 % (target 95 %; not tuned). E7 12/12 graceful. E8 0 unsafe, 0 actions,
0 CMM (but the model repeated planted claims; two gate rules added). E9 0 leaks. E10 trained: heater, fan, overvoltage
5/5 each, drift 0/5 (rules also 0/5). E6 text first chunk p50 4.6 s before the focused prompt and the 6 s watchdog.

## Owner questions / actions

- Decision 0021: keep the renamed random seeds (default) or restore them at run time?
- Approve the 20 ROM documents (citations in real use depend on it); the persona search found 0 eligible
  configurations, so there is no blind sheet; record the backup video; carry `interface/web/public/assets/farm/`.
- Alerts flip when the hvac brain flips (one owner alert per flip): calm alerts per node, or keep as specified?
